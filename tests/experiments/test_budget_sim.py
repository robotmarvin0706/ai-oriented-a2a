"""Unit tests for ``experiments/budget/sim.py`` (PREREG_BUDGET §6-§8)."""

from __future__ import annotations

import copy
import inspect
import json

import pytest

from experiments.budget import sim
from experiments.budget.sim import (
    ROUTE_FAST_ACCEPT,
    ROUTE_FAST_REJECT,
    ROUTE_FULL_GATE,
)
from experiments.budget.stream import (
    ACCEPT_MARGIN,
    SPOT_CHECK_N,
    build_stream,
    eligibility,
    source_of,
)
from experiments.timeout_recovery.host import DSR_THRESHOLD, Episode

RUN = 0
RUN_S = 3  # run 3 of seed 42 has K_S = 1 (dishonest peer present)
SEED = 42


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def stream_of(run: int = RUN, seed: int = SEED):
    return build_stream(seed, run)


def by_class(stream):
    out = {}
    for packet in stream.packets:
        out.setdefault(stream.truth_of(packet).cls, []).append(packet)
    return out


def state_of(label: str, run: int = RUN, stream=None):
    stream = stream or stream_of(run)
    return sim.new_state(label, SEED, run, stream, sim.EvalCache(stream.run_seed))


def episode(success: bool, tokens: int = 80, window: str = "spot_check", index: int = 0) -> Episode:
    return Episode(window, index, success, "ok" if success else "max_attempts", 2, tokens, 100, [])


def absorb_tokens_of(packet) -> int:
    return sim.approx_tokens(sim.wrap_untrusted(packet))


# ---------------------------------------------------------------------------
# Routes (§6.3) and the reference SR (§6.2)
# ---------------------------------------------------------------------------


def test_route_boundaries_are_exact() -> None:
    r = 0.50
    eps = 1e-9
    assert sim.route_of(r + DSR_THRESHOLD - eps, r) == ROUTE_FAST_REJECT
    assert sim.route_of(r + DSR_THRESHOLD, r) == ROUTE_FULL_GATE
    assert sim.route_of(r + ACCEPT_MARGIN - eps, r) == ROUTE_FULL_GATE
    assert sim.route_of(r + ACCEPT_MARGIN, r) == ROUTE_FAST_ACCEPT
    assert sim.route_of(r - 0.40, r) == ROUTE_FAST_REJECT


def test_route_for_packet_takes_the_packet_only() -> None:
    names = set(inspect.signature(sim.route_for_packet).parameters)
    assert names == {"packet", "reference", "flagged_sources", "guard"}
    assert set(inspect.signature(sim.route_of).parameters) == {"value", "reference"}
    source = inspect.getsource(sim.route_for_packet) + inspect.getsource(sim.route_of)
    assert "truth" not in source and "stream" not in source

    stream = stream_of()
    packet = copy.deepcopy(by_class(stream)["E"][0])
    packet["verification_claim"]["value"] = 0.55
    route, code, value = sim.route_for_packet(packet, 0.30)
    assert (route, code, value) == (ROUTE_FAST_ACCEPT, None, 0.55)
    packet["verification_claim"]["value"] = 0.31
    assert sim.route_for_packet(packet, 0.30)[0] == ROUTE_FAST_REJECT
    packet["verification_claim"]["value"] = 0.40
    assert sim.route_for_packet(packet, 0.30)[0] == ROUTE_FULL_GATE

    packet["verification_claim"]["repro"]["n"] = 5
    assert sim.route_for_packet(packet, 0.30)[:2] == (ROUTE_FULL_GATE, "repro_ineligible:n")
    del packet["verification_claim"]
    assert sim.route_for_packet(packet, 0.30)[:2] == (ROUTE_FULL_GATE, "repro_ineligible:no_claim")


def test_undefined_reference_until_twelve_episodes() -> None:
    assert sim.reference_of([]) is None
    assert sim.reference_of([True] * 11) is None
    assert sim.reference_of([True] * 12) == 1.0
    assert sim.reference_of([False] * 6 + [True] * 6) == 0.5
    assert sim.reference_of([False] * 10 + [True] * 60) == 1.0  # last min(60, k) only
    assert sim.recent_mean([True] * 11) is None
    assert sim.recent_mean([False] * 6 + [True] * 6) == 0.5

    stream = stream_of()
    packet = copy.deepcopy(by_class(stream)["E"][0])
    route, code, _value = sim.route_for_packet(packet, sim.reference_of([True] * 6))
    assert (route, code) == (ROUTE_FULL_GATE, "repro_no_reference")


def test_probation_threshold_includes_the_value_term() -> None:
    assert sim.probation_threshold(0.30, 0.90) == pytest.approx(0.60)
    assert sim.probation_threshold(0.30, 0.20) == pytest.approx(0.35)
    assert sim.probation_threshold(0.30, None) == pytest.approx(0.35)
    assert sim.probation_threshold(0.80, 0.90) == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# D2 spot-check, escalation and probation (§6.4)
# ---------------------------------------------------------------------------


def test_spot_check_pass_installs_and_opens_probation(monkeypatch) -> None:
    stream = stream_of()
    state = state_of("D2", stream=stream)
    packet = by_class(stream)["E"][0]
    absorb = absorb_tokens_of(packet)
    windows = []

    def fake(policy, case, window, index):
        windows.append(window)
        return episode(True, 80, window, index)

    monkeypatch.setattr(sim, "run_episode", fake)
    before = state.host.policy.label()
    sim._guarded_fast_accept(state, packet, 11, 0.30, 0.90)
    assert windows == [sim.SPOT_CHECK_WINDOW] * SPOT_CHECK_N
    assert state.host.policy.label() != before
    assert state.host.policy.context_tokens == absorb
    assert state.probation is not None
    assert state.probation["packet_id"] == packet["id"]
    assert state.probation["theta"] == pytest.approx(0.60)
    assert state.count("spot_check_pass") == 1
    assert state.count("escalation") == 0
    assert state.repro_tokens == absorb + SPOT_CHECK_N * 80
    assert state.gate_tokens == 0
    assert state.flags == set()


def test_spot_check_fail_flags_source_and_escalates_charging_the_block_twice(monkeypatch) -> None:
    stream = stream_of(RUN_S)
    state = state_of("D2", run=RUN_S, stream=stream)
    packet = by_class(stream)["S"][0]
    absorb = absorb_tokens_of(packet)
    monkeypatch.setattr(sim, "run_episode",
                        lambda policy, case, window, index: episode(False, 80, window, index))
    before = state.host.policy.label()
    sim._guarded_fast_accept(state, packet, 11, 0.30, 0.90)
    assert source_of(packet) in state.flags
    assert state.count("escalation") == 1
    assert state.count("spot_check_fail") == 1
    assert state.count("spot_check_fail_harmful") == 1
    assert state.probation is None
    assert state.repro_tokens == absorb + SPOT_CHECK_N * 80
    record = state.host.log[-1]
    assert record.absorb_tokens == absorb  # absorb block charged again inside the gate
    assert state.gate_tokens >= absorb + record.isolation_tokens
    assert state.host.policy.label() == before  # the gate never absorbs a harmful packet here


def test_probation_rollback_flags_source_and_charges_three_replays(monkeypatch) -> None:
    stream = stream_of()
    state = state_of("D2", stream=stream)
    packet = by_class(stream)["E"][0]
    monkeypatch.setattr(sim, "run_episode",
                        lambda policy, case, window, index: episode(True, 80, window, index))
    sim._guarded_fast_accept(state, packet, 11, 0.30, 0.90)
    installed = state.host.policy.label()
    state.task_successes = [[False] * 6] * 12  # tasks T1..T12, all failing
    monkeypatch.setattr(sim, "run_window",
                        lambda policy, case, window: [episode(False, 100, window, i) for i in range(3)])
    repro_before = state.repro_tokens
    sim._check_probation(state, 11 + sim.PROBATION_TASKS)
    assert state.probation is None
    assert state.repro_tokens == repro_before + 300  # 3 replays of 100 tokens
    assert source_of(packet) in state.flags
    assert state.count("probation_rollback") == 1
    assert state.count("probation_rollback_false") == 1  # an effective packet was rolled back
    assert state.host.policy.label() != installed
    assert state.host.policy.context_tokens == 0
    assert [e["event"] for e in state.events][-1] == "probation_rolled_back"
    assert state.events[-1]["packet_id"] == packet["id"]


def test_probation_passes_above_theta_and_supersedes_on_a_new_change(monkeypatch) -> None:
    stream = stream_of()
    state = state_of("D2", stream=stream)
    packets = by_class(stream)["Mm"]
    monkeypatch.setattr(sim, "run_episode",
                        lambda policy, case, window, index: episode(True, 80, window, index))
    sim._guarded_fast_accept(state, packets[0], 11, 0.30, 0.50)
    state.task_successes = [[True] * 6] * 12
    sim._check_probation(state, 11 + sim.PROBATION_TASKS)
    assert state.count("probation_pass") == 1
    assert state.count("probation_rollback") == 0
    assert state.flags == set()

    sim._guarded_fast_accept(state, packets[1], 20, 0.30, 0.50)
    assert state.probation is not None
    sim._register_change(state, packets[2], 21, ROUTE_FAST_ACCEPT)
    assert state.count("probation_superseded") == 1
    assert state.probation is None


def test_flagged_source_is_ineligible_for_d2_only(monkeypatch) -> None:
    stream = stream_of(RUN_S)
    packet = by_class(stream)["S"][0]
    source = source_of(packet)
    assert eligibility(packet, frozenset({source}), check_flags=True) == (False, "flagged_source")
    assert eligibility(packet, frozenset({source}), check_flags=False) == (True, None)
    assert sim.route_for_packet(packet, 0.0, frozenset({source}), guard=True)[0] == ROUTE_FULL_GATE
    assert sim.route_for_packet(packet, 0.0, frozenset({source}), guard=False)[0] == ROUTE_FAST_ACCEPT

    d2 = state_of("D2", run=RUN_S, stream=stream)
    d2.flags.add(source)
    monkeypatch.setattr(sim, "run_episode",
                        lambda policy, case, window, index: episode(False, 80, window, index))
    sim._handle_repro_candidate(d2, by_class(stream)["S"][1], 12)
    assert any(e["event"] == "ineligible" and e["reason"] == "repro_ineligible:flagged_source"
               for e in d2.events)
    assert d2.count("fast_accept") == 0

    d1 = state_of("D1", run=RUN_S, stream=stream)
    d1.flags.add(source)
    sim._blind_install(d1, by_class(stream)["S"][1], 12)
    assert d1.count("spot_check_pass") == 0 and d1.count("spot_check_fail") == 0


# ---------------------------------------------------------------------------
# Whole-run behaviour (§7, §8)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def run0():
    return sim.run_endpoints(SEED, RUN)


@pytest.fixture(scope="module")
def run3():
    return sim.run_endpoints(SEED, RUN_S)


REQUIRED_KEYS = {
    "plateau_SR", "full_sequence_SR", "total_tokens", "task_tokens", "gate_tokens",
    "repro_overhead_tokens", "verification_tokens", "resident_context_tokens_T100",
    "harmful_absorbed", "harmful_installations", "persistent_harm", "tasks_on_harmful_policy",
    "harmful_fast_accepted", "harmful_spot_check_passed", "harmful_gate_absorbed",
    "harmful_rolled_back", "n_fast_reject", "n_fast_accept", "fast_path_rate",
    "false_fast_path", "spot_check_pass", "spot_check_fail", "escalations",
    "probation_rollbacks_true", "probation_rollbacks_false", "probation_superseded",
    "budget_rejections", "rollbacks", "attributed", "k_e", "k_s", "seed", "run",
}


def test_endpoints_are_complete_and_json_serialisable(run0) -> None:
    assert set(run0) == {"A", "C", "D1", "D2", "V1", "V2"}
    for label, endpoint in run0.items():
        assert REQUIRED_KEYS <= set(endpoint)
        assert endpoint["arm"] == label
        assert endpoint["seed"] == SEED and endpoint["run"] == RUN
        json.dumps(endpoint)  # JSON-serialisable
        assert "time" not in json.dumps(endpoint).lower().replace("timeout", "")


def test_token_identity_for_every_arm(run0) -> None:
    for endpoint in run0.values():
        assert endpoint["verification_tokens"] == (
            endpoint["gate_tokens"] + endpoint["repro_overhead_tokens"])
        assert endpoint["total_tokens"] == endpoint["task_tokens"] + endpoint["verification_tokens"]
        assert endpoint["total_tokens"] == (
            endpoint["task_tokens"] + endpoint["gate_tokens"] + endpoint["repro_overhead_tokens"])


def test_c_and_a_are_not_charged_claim_reads(run0) -> None:
    for label in ("A", "C"):
        assert run0[label]["repro_overhead_tokens"] == 0
    assert run0["A"]["gate_tokens"] == 0 and run0["A"]["n_fast_reject"] == 0
    assert run0["A"]["n_fast_accept"] == 0
    assert run0["C"]["gate_attempts"] == 40
    assert run0["D2"]["repro_overhead_tokens"] > 0


def test_v1_charges_absorb_blocks_only_and_stays_naive(run0) -> None:
    stream = stream_of()
    assert run0["V1"]["gate_tokens"] == sum(absorb_tokens_of(p) for p in stream.packets)
    assert run0["V1"]["repro_overhead_tokens"] == 0
    assert run0["V1"]["plateau_SR"] == run0["A"]["plateau_SR"]
    assert run0["V1"]["full_sequence_SR"] == run0["A"]["full_sequence_SR"]
    assert run0["V1"]["policy_changes"] == 0
    assert run0["V1"]["resident_context_tokens_T100"] == 0


def test_d1_never_spot_checks_or_flags(run3) -> None:
    d1 = run3["D1"]
    assert sum(d1["spot_check_pass"].values()) == 0
    assert sum(d1["spot_check_fail"].values()) == 0
    assert d1["escalations"] == 0
    assert d1["probation_superseded"] == 0
    assert d1["probation_rollbacks_true"] + d1["probation_rollbacks_false"] == 0
    assert d1["flagged_sources"] == []
    assert d1["rollbacks"]["probation"] == 0


def test_v2_has_d2_fast_path_counts(run0, run3) -> None:
    for endpoints in (run0, run3):
        assert endpoints["V2"]["n_fast_reject"] == endpoints["D2"]["n_fast_reject"]
        assert endpoints["V2"]["n_fast_accept"] == endpoints["D2"]["n_fast_accept"]
        assert endpoints["V2"]["fast_path_rate"] == (
            endpoints["D2"]["n_fast_reject"] + endpoints["D2"]["n_fast_accept"]) / 40


def test_paired_draws_and_identical_gate_decisions() -> None:
    stream = stream_of()
    states = {label: sim.run_arm(label, SEED, RUN, stream) for label in ("A", "C", "D2")}
    naive = states["A"].task_successes[:10]  # T1..T10: every arm is on the naive policy
    assert states["C"].task_successes[:10] == naive
    assert states["D2"].task_successes[:10] == naive

    packet = by_class(stream)["Mm"][0]
    first, second = state_of("C", stream=stream), state_of("D2", stream=stream)
    out1, out2 = sim._gate(first, packet, 11), sim._gate(second, packet, 11)
    assert (out1["status"], out1["reason"]) == (out2["status"], out2["reason"])
    assert out1["tokens"] == out2["tokens"]


def test_deterministic_and_free_of_wall_clock_times() -> None:
    first = sim.run_endpoints(SEED, RUN)
    second = sim.run_endpoints(SEED, RUN)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert sim.ARM_ORDER.index("D2") < sim.ARM_ORDER.index("V2")


def test_routing_never_reads_stream_truth() -> None:
    for function in (sim.route_for_packet, sim.route_of, sim.probation_threshold,
                     sim._fast_reject, sim._handle_repro_candidate):
        source = inspect.getsource(function)
        assert "truth" not in source and "harmful_of" not in source and "stream" not in source
    assert "route_for_packet" in inspect.getsource(sim._handle_repro_candidate)
