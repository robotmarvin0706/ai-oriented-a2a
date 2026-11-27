"""Unit tests for the experiment-3 candidate stream and claim parser.

Implements the checks of ``docs/PREREG_BUDGET.md`` §5.2, §5.3 and §6.1 that can
be made without running the experiment. Fast: a stream is built in well under
0.1 s, so a handful of ``(seed, run)`` pairs are enough.
"""

from __future__ import annotations

import copy
import json
import re

import pytest

from ai_oriented_a2a import content_policy_rejection

from experiments.budget import stream as bs

REPO = __import__("pathlib").Path(__file__).resolve().parents[2]

PAIRS = ((42, 0), (42, 1), (7, 0), (7, 3), (2026, 0), (2026, 4), (31337, 2), (31337, 7))
SENT_FIELDS = {"id", "version", "type", "content", "absorption", "provenance", "verification_claim"}


@pytest.fixture(scope="module")
def streams():
    return [bs.build_stream(seed, run) for seed, run in PAIRS]


@pytest.fixture(scope="module")
def packets(streams):
    return [(s, p) for s in streams for p in s.packets]


# ---------------------------------------------------------------------------
# Packets: content policy, schema, shape (§5.2, §5.3)
# ---------------------------------------------------------------------------


def test_packets_pass_content_policy_and_schema(packets):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((REPO / "schema" / "experience-packet.schema.json").read_text())
    for _stream, pkt in packets:
        assert content_policy_rejection(pkt) is None, pkt["id"]
        jsonschema.validate(pkt, schema)


def test_packet_envelope_shape(packets):
    for _stream, pkt in packets:
        assert set(pkt) <= SENT_FIELDS
        assert pkt["version"] == "0.4"
        assert pkt["type"] == "failure_mode"
        assert pkt["absorption"] == {"method": "prompt_injection", "target": "tool_retry_policy"}
        for absent in ("prior_outcomes", "expiry", "replay_hint"):
            assert absent not in pkt
        assert set(pkt["provenance"]) == {"source_ref"}
        claim = pkt.get("verification_claim")
        if claim is not None:
            assert "replay_hint" not in claim


def test_sources_match_class(packets):
    for stream, pkt in packets:
        truth = stream.truth_of(pkt)
        source = pkt["provenance"]["source_ref"]
        if truth.cls == bs.CLASS_EFFECTIVE:
            assert source == "peer:peer-eff-1"
        elif truth.cls == bs.CLASS_MISMATCHED:
            assert source == "peer:peer-other-1"
        elif truth.cls == bs.CLASS_SPOOFED:
            assert source == "peer:peer-dis-1"
        else:
            assert source in ("peer:peer-med-1", "peer:peer-med-2")


def test_content_families(packets):
    for stream, pkt in packets:
        params = pkt["content"]["recovery_policy"]
        family = stream.truth_of(pkt).family
        if family == "effective":
            assert params == {"backoff": "exponential", "base_backoff_ms": 400, "max_attempts": 4,
                              "drop_stale_partials": True}
        elif family == "mediocre":
            assert params["backoff"] == "exponential"
            assert params["base_backoff_ms"] in (50, 100)
            assert params["max_attempts"] in (2, 3)
            assert params["drop_stale_partials"] is False
        else:
            assert params["backoff"] == "none"
            assert params["max_attempts"] in (6, "unbounded")
            assert params["drop_stale_partials"] is False


def test_ids_are_opaque_and_unique(streams, packets):
    pattern = re.compile(r"^pkt-bud-[0-9a-f]{16}$")
    for stream in streams:
        assert len(set(stream.arrival_order)) == bs.N_CANDIDATES
        assert set(stream.arrival_order) == set(stream.truth)
    for _stream, pkt in packets:
        pid = pkt["id"]
        assert pattern.match(pid), pid
        lowered = pid.lower()
        # Only words with a non-hex character can be excluded: "eff"/"med" are
        # all hex digits and may occur by chance inside an opaque id.
        for word in ("peer", "dis", "other", "effective", "mediocre", "harmful", "source"):
            assert word not in lowered, pid


# ---------------------------------------------------------------------------
# Stream shape, indicators and arrival order (§5.1, §5.2, §9.4)
# ---------------------------------------------------------------------------


def test_total_is_always_40():
    for k_e in (0, 1):
        for k_s in (0, 1):
            specs = bs.class_specs(k_e, k_s)
            assert len(specs) == bs.N_CANDIDATES == 40


def test_class_counts_follow_indicators(streams):
    for stream in streams:
        k_e, k_s = stream.k_e, stream.k_s
        assert stream.k_e == bs.stream_indicators(stream.seed, stream.run)[0]
        assert stream.k_s == bs.stream_indicators(stream.seed, stream.run)[1]
        assert stream.run_seed == bs.run_seed_of(stream.seed, stream.run)
        assert stream.class_counts() == {
            bs.CLASS_EFFECTIVE: k_e,
            bs.CLASS_MEDIOCRE: 18 - 4 * k_s,
            bs.CLASS_MISMATCHED: 10,
            bs.CLASS_SPOOFED: 4 * k_s,
            bs.CLASS_NO_REPRO: 12 - k_e,
        }
        assert len(stream.packets) == bs.N_CANDIDATES


def test_mismatch_split():
    specs = bs.class_specs(1, 1)
    x = [(mismatch, family) for cls, family, mismatch, _src in specs if cls == bs.CLASS_MISMATCHED]
    assert len(x) == 10
    assert sum(1 for m, _f in x if m == "model") == 4
    assert sum(1 for m, _f in x if m == "scenario") == 3
    assert sum(1 for m, _f in x if m == "n") == 3
    assert sum(1 for _m, f in x if f == "harmful") == 5
    assert sum(1 for _m, f in x if f == "mediocre") == 5


def test_indicator_rates_are_about_half():
    draws = [bs.stream_indicators(seed, run) for seed in bs.SEEDS for run in range(bs.RUNS)]
    assert len(draws) == bs.RUNS * len(bs.SEEDS) == 160
    k_e = sum(a for a, _b in draws) / len(draws)
    k_s = sum(b for _a, b in draws) / len(draws)
    assert 0.40 <= k_e <= 0.60
    assert 0.40 <= k_s <= 0.60


def test_stream_is_deterministic():
    a = json.dumps([p for p in bs.build_stream(7, 5).packets], sort_keys=True)
    b = json.dumps([p for p in bs.build_stream(7, 5).packets], sort_keys=True)
    assert a == b
    assert a != json.dumps([p for p in bs.build_stream(7, 6).packets], sort_keys=True)


def test_arrival_order_is_shared_and_deterministic():
    first = bs.build_stream(42, 0)
    again = bs.build_stream(42, 0)
    assert first.arrival_order == again.arrival_order
    assert sorted(first.arrival_order) == sorted(first.truth)
    assert first.arrival_order != tuple(sorted(first.arrival_order))  # a real permutation
    assert [first.arrival_task(i) for i in range(bs.N_CANDIDATES)] == list(range(11, 51))
    assert bs.FIRST_ARRIVAL == 11 and bs.PLATEAU == (61, 100)


def test_registered_constants():
    assert bs.SEEDS == (42, 7, 2026, 31337)
    assert (bs.RUNS, bs.TASKS, bs.N_CANDIDATES, bs.FIRST_ARRIVAL) == (40, 100, 40, 11)
    assert (bs.Q_E, bs.Q_S) == (0.5, 0.5)
    assert bs.HOST_SCENARIO_REF == "timeout_recovery/sim-v1/naive_host"
    assert (bs.ACCEPT_MARGIN, bs.SPOT_CHECK_N, bs.PROBATION_TOL) == (0.20, 4, 0.30)
    assert (bs.REFERENCE_MIN, bs.REFERENCE_MAX, bs.PRIOR_CASES) == (12, 60, 30)
    assert bs.run_seed_of(42, 0) == bs.run_seed_of(42, 0) != bs.run_seed_of(42, 1)


# ---------------------------------------------------------------------------
# Claims (§5.3)
# ---------------------------------------------------------------------------


def test_honest_claims_are_matching_and_in_range(streams, packets):
    for stream, pkt in packets:
        truth = stream.truth_of(pkt)
        if truth.cls not in (bs.CLASS_EFFECTIVE, bs.CLASS_MEDIOCRE):
            continue
        claim = pkt["verification_claim"]
        assert claim["metric"] == "recovery_sr"
        assert claim["scenario_ref"] == bs.HOST_SCENARIO_REF
        assert claim["expected_effect"]
        assert 0.0 <= claim["value"] <= 1.0
        assert claim["repro"] == {"seed": stream.run_seed, "model": bs.MODEL_NAME, "n": 30}
        assert bs.eligibility(pkt)[0] is True


def test_honest_value_is_reproducible(streams):
    stream = streams[0]
    pkt = next(p for p in stream.packets if stream.truth_of(p).cls == bs.CLASS_EFFECTIVE)
    assert bs.honest_value(stream.seed, stream.run, pkt) == pkt["verification_claim"]["value"]


def test_effective_claim_is_above_mediocre(streams):
    effective, mediocre = [], []
    for stream, pkt in [(s, p) for s in streams for p in s.packets]:
        cls = stream.truth_of(pkt).cls
        if cls == bs.CLASS_EFFECTIVE:
            effective.append(pkt["verification_claim"]["value"])
        elif cls == bs.CLASS_MEDIOCRE:
            mediocre.append(pkt["verification_claim"]["value"])
    assert effective, "no effective packet in the test runs"
    assert sum(effective) / len(effective) > 0.85
    assert sum(mediocre) / len(mediocre) < 0.60
    assert sum(effective) / len(effective) - sum(mediocre) / len(mediocre) > 0.30


def test_mismatched_and_forged_claims(streams, packets):
    for stream, pkt in packets:
        truth = stream.truth_of(pkt)
        if truth.cls not in (bs.CLASS_MISMATCHED, bs.CLASS_SPOOFED):
            continue
        claim = pkt["verification_claim"]
        assert claim["metric"] == "recovery_sr"
        assert 0.85 <= claim["value"] <= 0.95
        assert claim["repro"]["seed"] == stream.run_seed
        if truth.cls == bs.CLASS_SPOOFED:
            assert claim["repro"] == {"seed": stream.run_seed, "model": bs.MODEL_NAME, "n": 100}
            assert bs.eligibility(pkt)[0] is True  # forged but well formed (§5.3)
        elif truth.mismatch == "model":
            assert claim["repro"]["model"] == "llm-receiver-v0"
        elif truth.mismatch == "scenario":
            assert claim["scenario_ref"] == "timeout_recovery/sim-v1/working_host"
        else:
            assert claim["repro"]["n"] in (5, 10)


def test_no_repro_class(streams):
    for stream in streams:
        m0 = [p for p in stream.packets if stream.truth_of(p).cls == bs.CLASS_NO_REPRO]
        with_claim = [p for p in m0 if "verification_claim" in p]
        assert len(with_claim) == bs.M0_WITH_CLAIM
        for pkt in with_claim:
            assert pkt["verification_claim"] == {"expected_effect": bs.EXPECTED_EFFECT}
        for pkt in m0:
            if "verification_claim" not in pkt:
                assert bs.eligibility(pkt) == (False, "no_claim")


def test_prior_cases_never_coincide_with_tasks(streams):
    # Task t of a run is Case(run_seed, t); prior cases are Case(prior_seed, 0).
    for stream in streams:
        for pkt in stream.packets[:5]:
            for j in range(bs.PRIOR_CASES):
                case = bs.prior_case(stream.seed, stream.run, pkt["id"], j)
                assert case.index == 0
                assert case.seed != stream.run_seed
        # A distinct prior stream: (seed, run, packet id, j) all take part in the draw.
        other = bs.prior_case(stream.seed, stream.run, "pkt-bud-0000000000000000", 0)
        assert other.seed != bs.prior_case(stream.seed, stream.run, "pkt-bud-0000000000000001", 0).seed


# ---------------------------------------------------------------------------
# Claim parser (§6.1)
# ---------------------------------------------------------------------------

SOURCE = "peer:peer-eff-1"


def _claim(**over):
    claim = {
        "expected_effect": "recover from timeouts",
        "scenario_ref": bs.HOST_SCENARIO_REF,
        "metric": "recovery_sr",
        "value": 0.9,
        "repro": {"seed": 12345, "model": bs.MODEL_NAME, "n": 30},
    }
    claim.update(over)
    return claim


def _packet(claim, source=SOURCE):
    pkt = {
        "id": "pkt-bud-0123456789abcdef",
        "version": "0.4",
        "type": "failure_mode",
        "content": {},
        "absorption": {"method": "prompt_injection", "target": "tool_retry_policy"},
        "provenance": {"source_ref": source},
    }
    if claim is not None:
        pkt["verification_claim"] = claim
    return pkt


@pytest.mark.parametrize("bad_claim,reason", [
    (None, "no_claim"),
    ("recover from timeouts", "no_claim"),
    (["recovery_sr"], "no_claim"),
    (_claim(metric="sr"), "metric"),
    (_claim(metric=None), "metric"),
    (_claim(scenario_ref="timeout_recovery/sim-v1/working_host"), "scenario"),
    (_claim(value=True), "value"),
    (_claim(value=False), "value"),
    (_claim(value="0.9"), "value"),
    (_claim(value=-0.01), "value"),
    (_claim(value=1.01), "value"),
    (_claim(value=None), "value"),
    (_claim(repro=None), "no_repro"),
    (_claim(repro=[1, 2]), "no_repro"),
    (_claim(repro="deterministic-rule-host-v1"), "no_repro"),
    (_claim(repro={"seed": 1, "model": "llm-receiver-v0", "n": 30}), "model"),
    (_claim(repro={"seed": 1, "model": bs.MODEL_NAME, "n": True}), "n"),
    (_claim(repro={"seed": 1, "model": bs.MODEL_NAME, "n": 30.0}), "n"),
    (_claim(repro={"seed": 1, "model": bs.MODEL_NAME, "n": 29}), "n"),
    (_claim(repro={"seed": 1, "model": bs.MODEL_NAME}), "n"),
    (_claim(repro={"seed": True, "model": bs.MODEL_NAME, "n": 30}), "seed"),
    (_claim(repro={"seed": 12.0, "model": bs.MODEL_NAME, "n": 30}), "seed"),
    (_claim(repro={"model": bs.MODEL_NAME, "n": 30}), "seed"),
])
def test_ineligibility_reasons(bad_claim, reason):
    assert bs.eligibility(_packet(bad_claim)) == (False, reason)


def test_eligibility_order_and_flags():
    both_bad = _claim(metric="sr", scenario_ref="elsewhere")
    assert bs.eligibility(_packet(both_bad)) == (False, "metric")
    good = _packet(_claim())
    assert bs.eligibility(good) == (True, None)
    assert bs.eligibility(good, flagged_sources=frozenset({SOURCE})) == (False, "flagged_source")
    assert bs.eligibility(good, flagged_sources=frozenset({"peer:peer-other-1"})) == (True, None)
    assert bs.eligibility(good, flagged_sources=frozenset({SOURCE}), check_flags=False) == (True, None)
    # A missing claim is reported before a source flag.
    assert bs.eligibility(_packet(None), flagged_sources=frozenset({SOURCE})) == (False, "no_claim")
    assert bs.ineligible_code("flagged_source") == "repro_ineligible:flagged_source"
    assert set(bs.ELIGIBILITY_REASONS) == {
        "no_claim", "metric", "scenario", "value", "no_repro", "model", "n", "seed", "flagged_source"}


def test_stream_claims_parse_as_expected(streams):
    for stream, pkt in [(s, p) for s in streams for p in s.packets]:
        truth = stream.truth_of(pkt)
        eligible, reason = bs.eligibility(pkt)
        if truth.cls in (bs.CLASS_EFFECTIVE, bs.CLASS_MEDIOCRE, bs.CLASS_SPOOFED):
            assert (eligible, reason) == (True, None)
        elif truth.cls == bs.CLASS_MISMATCHED:
            assert (eligible, reason) == (False, truth.mismatch)
        else:
            assert (eligible, reason) in ((False, "no_claim"), (False, "metric"))


def test_claim_read_tokens(streams):
    absent = expected_only = None
    honest = []
    for stream, pkt in [(s, p) for s in streams for p in s.packets]:
        tokens = bs.claim_read_tokens(pkt)
        claim = pkt.get("verification_claim")
        if claim is None:
            absent = tokens
        elif "repro" not in claim:
            expected_only = tokens
        elif stream.truth_of(pkt).cls in (bs.CLASS_EFFECTIVE, bs.CLASS_MEDIOCRE):
            honest.append(tokens)
    assert absent == 6  # §7.4
    assert 25 <= expected_only <= 45
    assert honest and 68 <= min(honest) and max(honest) <= 82
