"""SPEC §3 core constraints and negotiation round cap (protocol v0.4).

- Schemas: OPTIONAL ``core_constraints`` (array of strings) on the Agent
  Profile; OPTIONAL ``negotiation_id`` (non-empty string) on all four
  negotiation branches; OPTIONAL ``target_constraint`` on counter. Old
  messages without them still validate; ``additionalProperties`` stays false.
- ``ai_oriented_a2a.negotiation`` (reference) and ``second_impl`` (written
  separately) agree: same default cap (3), same reason prefixes, same
  decisions on the same message sequences, and the same order
  (content_policy, negotiation_limit, immutable_constraint).
- The cap is a named, overridable constant; messages without
  ``negotiation_id`` are never counted; the table is bounded; the per-peer
  rate limit is off by default.

Python 3.8 compatible (no ``dict |``).
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "examples"
for _p in (str(REPO), str(EXAMPLES)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ai_oriented_a2a  # noqa: E402
from ai_oriented_a2a import negotiation as ref_neg  # noqa: E402
from ai_oriented_a2a.negotiation import NegotiationGuard, immutable_constraint_rejection  # noqa: E402
from second_impl import a2a_core as core  # noqa: E402

CORE = "no_external_payment"
INJECT = json.loads(
    (REPO / "datasets" / "content_policy" / "packets" / "pkt-synth-inject-001.json").read_text(encoding="utf-8")
)


def _load(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def _propose(target="tool_whitelist", nid=None, pid="prop-1"):
    msg = {"proposal_id": pid, "target_constraint": target, "proposed_change": "relax it"}
    if nid is not None:
        msg["negotiation_id"] = nid
    return msg


def _counter(nid=None, pid="prop-2", target=None):
    msg = {"proposal_id": pid, "status": "counter", "revised_change": "smaller change"}
    if nid is not None:
        msg["negotiation_id"] = nid
    if target is not None:
        msg["target_constraint"] = target
    return msg


# --- schemas ------------------------------------------------------------------

try:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    HAVE_SCHEMA = True
except ImportError:  # pragma: no cover - exercised on minimal installs
    HAVE_SCHEMA = False

needs_schema = pytest.mark.skipif(not HAVE_SCHEMA, reason="jsonschema not installed")


def _validator(name):
    resources = []
    for path in (REPO / "schema").glob("*.schema.json"):
        contents = json.loads(path.read_text(encoding="utf-8"))
        resource = Resource.from_contents(contents)
        resources.append((path.name, resource))
        if contents.get("$id"):
            resources.append((contents["$id"], resource))
    schema = json.loads((REPO / "schema" / name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema, registry=Registry().with_resources(resources))


@needs_schema
@pytest.mark.parametrize(
    "name",
    ["negotiation-propose.json", "negotiation-accept.json", "negotiation-reject.json", "negotiation-counter.json"],
)
def test_negotiation_id_optional_on_every_branch(name):
    v = _validator("negotiation.schema.json")
    msg = _load(name)
    assert "negotiation_id" not in msg
    v.validate(msg)  # old message without negotiation_id
    msg["negotiation_id"] = "neg-1"
    v.validate(msg)
    msg["negotiation_id"] = ""
    assert not v.is_valid(msg)
    msg["negotiation_id"] = 3
    assert not v.is_valid(msg)


@needs_schema
def test_counter_target_constraint_optional_and_unknown_fields_still_rejected():
    v = _validator("negotiation.schema.json")
    counter = _load("negotiation-counter.json")
    counter["target_constraint"] = CORE
    v.validate(counter)
    counter["peer_history"] = []
    assert not v.is_valid(counter)
    accept = _load("negotiation-accept.json")
    accept["target_constraint"] = CORE
    assert not v.is_valid(accept)


@needs_schema
def test_profile_core_constraints_optional_array_of_strings():
    v = _validator("agent-profile.schema.json")
    profile = _load("agent-profile.json")
    v.validate(profile)
    profile["core_constraints"] = [CORE]
    v.validate(profile)
    profile["core_constraints"] = CORE
    assert not v.is_valid(profile)
    profile["core_constraints"] = [1]
    assert not v.is_valid(profile)
    profile["core_constraints"] = []
    profile["sandbox_required"] = True
    assert not v.is_valid(profile)


@needs_schema
def test_updated_examples_validate():
    _validator("agent-profile.schema.json").validate(_load("agent-profile-with-identity.json"))
    _validator("negotiation.schema.json").validate(_load("negotiation-propose-conditional.json"))


# --- constants and API --------------------------------------------------------


def test_default_cap_is_three_in_both_implementations():
    assert ref_neg.DEFAULT_MAX_NEGOTIATION_ROUNDS == 3
    assert ai_oriented_a2a.DEFAULT_MAX_NEGOTIATION_ROUNDS == 3
    assert core.DEFAULT_ROUND_CAP == 3
    assert NegotiationGuard().max_rounds == 3
    assert core.NegotiationLimits().round_cap == 3


def test_reason_prefixes_match():
    assert ref_neg.IMMUTABLE_CONSTRAINT_PREFIX == core.REASON_IMMUTABLE + ":"
    assert ref_neg.NEGOTIATION_LIMIT_PREFIX == core.REASON_NEGOTIATION_LIMIT + ":"


@pytest.mark.parametrize("bad", [0, -1])
def test_invalid_caps_raise(bad):
    with pytest.raises(ValueError):
        NegotiationGuard(bad)
    with pytest.raises(ValueError):
        core.NegotiationLimits(bad)


# --- reference guard ----------------------------------------------------------


def test_reference_cap_and_override():
    guard = NegotiationGuard()
    results = [guard.check(_counter("n1", pid=f"p{i}")) for i in range(4)]
    assert results[:3] == [None, None, None]
    assert results[3]["status"] == "rejected"
    assert results[3]["reason"].startswith("negotiation_limit:")
    assert results[3]["packet_ref"] == "unknown"
    assert guard.rounds("n1") == 4

    five = NegotiationGuard(max_rounds=5)
    assert all(five.check(_counter("n1", pid=f"p{i}")) is None for i in range(5))
    assert five.check(_counter("n1"))["reason"].startswith("negotiation_limit:")


def test_reference_without_negotiation_id_never_counted():
    guard = NegotiationGuard(max_rounds=1)
    for i in range(10):
        assert guard.check(_counter(pid=f"p{i}")) is None
    for _ in range(10):
        assert guard.check(_counter(pid="same")) is None


def test_reference_rounds_counted_even_when_immutable():
    guard = NegotiationGuard()
    for i in range(3):
        rej = guard.check(_propose(CORE, "n1", pid=f"p{i}"), [CORE])
        assert rej["reason"].startswith("immutable_constraint:")
    assert guard.check(_propose(CORE, "n1"), [CORE])["reason"].startswith("negotiation_limit:")


def test_reference_table_is_bounded():
    guard = NegotiationGuard(max_tracked=4)
    for i in range(10):
        guard.check(_counter(f"n{i}"))
    assert guard.rounds("n0") == 0
    assert guard.rounds("n9") == 1
    assert len(guard._rounds) == 4


def test_reference_peer_rate_limit_off_by_default_and_hook_works():
    guard = NegotiationGuard(max_rounds=100)
    assert guard.peer_rate_limit is None
    for i in range(50):
        assert guard.check(_counter(pid=f"p{i}"), peer="1.2.3.4", now=1000.0) is None
    limited = NegotiationGuard(peer_rate_limit=2, peer_rate_window=10)
    assert limited.check(_counter(), peer="a", now=0.0) is None
    assert limited.check(_counter(), peer="a", now=1.0) is None
    assert limited.check(_counter(), peer="a", now=2.0)["reason"] == "negotiation_limit: peer rate limit exceeded"
    assert limited.check(_counter(), peer="b", now=2.0) is None
    assert limited.check(_counter(), peer="a", now=20.0) is None


def test_reference_immutable_only_for_propose_and_counter():
    assert immutable_constraint_rejection(_propose(CORE), [CORE])["reason"].startswith("immutable_constraint:")
    assert immutable_constraint_rejection(_counter(target=CORE), [CORE]) is not None
    assert immutable_constraint_rejection(_counter(), [CORE]) is None
    assert immutable_constraint_rejection({"proposal_id": "p", "status": "accept"}, [CORE]) is None
    assert immutable_constraint_rejection(_propose(CORE.upper()), [CORE]) is None
    assert immutable_constraint_rejection(_propose(CORE), []) is None
    ref = immutable_constraint_rejection(_load("negotiation-propose.json"), ["tool_whitelist"])
    assert ref["packet_ref"] == "pkt-offer-7f3a"


# --- parity -------------------------------------------------------------------


def _ref_decide(guard, msg, cores):
    policy = ai_oriented_a2a.content_policy_rejection(msg)
    limited = guard.check(msg, cores)
    if policy is not None:
        return policy["reason"].split(":", 1)[0], policy["packet_ref"]
    if limited is not None:
        return limited["reason"].split(":", 1)[0], limited["packet_ref"]
    return "negotiation_unsupported", None


def _second_decide(limits, msg, cores):
    reply = core.respond(msg, core_constraints=cores, limits=limits)
    return reply["reason"].split(":", 1)[0], (
        reply["packet_ref"] if not reply["reason"].startswith("negotiation_unsupported") else None
    )


def _offered(target, nid, pid):
    msg = _propose(target, nid, pid)
    msg["offered_packet"] = copy.deepcopy(_load("negotiation-propose.json")["offered_packet"])
    return msg


def _injected(target, nid, pid):
    msg = _propose(target, nid, pid)
    msg["offered_packet"] = copy.deepcopy(INJECT)
    return msg


SEQUENCES = {
    "immutable_then_limit": [_offered(CORE, "n1", f"p{i}") for i in range(5)],
    "content_policy_wins_over_immutable_and_limit": [_injected(CORE, "n2", f"p{i}") for i in range(5)],
    "mixed_chain": [
        _propose("tool_whitelist", "n3", "a"),
        _counter("n3", "b"),
        _counter("n3", "c", target=CORE),
        _counter("n3", "d"),
        _counter(None, "e", target=CORE),
        _counter("n4", "f"),
    ],
    "no_negotiation_id": [_counter(None, f"q{i}") for i in range(6)],
}


@pytest.mark.parametrize("name", sorted(SEQUENCES))
def test_reference_and_second_impl_agree(name):
    guard = NegotiationGuard()
    limits = core.NegotiationLimits()
    for msg in SEQUENCES[name]:
        assert _ref_decide(guard, msg, [CORE]) == _second_decide(limits, msg, [CORE]), msg


def test_expected_decisions_for_mixed_chain():
    limits = core.NegotiationLimits()
    got = [_second_decide(limits, m, [CORE])[0] for m in SEQUENCES["mixed_chain"]]
    assert got == [
        "negotiation_unsupported",
        "negotiation_unsupported",
        "immutable_constraint",
        "negotiation_limit",
        "immutable_constraint",
        "negotiation_unsupported",
    ]


def test_second_impl_respond_without_limits_is_unchanged():
    reply = core.respond(_counter("n1"))
    assert reply["reason"].startswith("negotiation_unsupported:")


# --- reference HTTP receiver --------------------------------------------------


def test_reference_profile_advertises_core_constraints():
    import network_dual_end

    saved = network_dual_end.A2AHandler.core_constraints
    try:
        network_dual_end.A2AHandler.core_constraints = []
        assert "core_constraints" not in network_dual_end.make_profile()
        network_dual_end.A2AHandler.core_constraints = [CORE]
        profile = network_dual_end.make_profile()
        assert profile["core_constraints"] == [CORE]
        if HAVE_SCHEMA:
            _validator("agent-profile.schema.json").validate(profile)
    finally:
        network_dual_end.A2AHandler.core_constraints = saved


def test_second_impl_cli_accepts_core_constraint_flags():
    from second_impl import minimal_client

    args = minimal_client.build_parser().parse_args(
        ["serve", "--core-constraint", CORE, "--core-constraint", "x", "--max-rounds", "5"]
    )
    assert args.core_constraint == [CORE, "x"]
    assert args.max_rounds == 5
