"""Unit tests for the expiry simulator (``experiments/expiry/sim.py``).

Focused and fast (no main run, no rate checks): the v2 lease rule of §5.2/§16.3,
the ``expiry`` predicate of §6.1/§16.2, the version timeline and peer feed of
§5.3/§5.4, the arm steps of §7 (incl. §7.1, §7.2, §7.3, §16.8-§16.11), the
paired design of §5 and determinism.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from experiments.expiry import sim  # noqa: E402
from experiments.timeout_recovery.host import (  # noqa: E402
    THROTTLE_MS,
    TIMEOUT_MS,
    Case,
    naive_policy,
    run_episode,
)
from experiments.timeout_recovery.packets import PEER_POLICY  # noqa: E402

NAIVE_LABEL = naive_policy().label()
P1_LABEL = PEER_POLICY.label()
P2_LABEL = sim.P2_POLICY.label()

#: A short, hand-built timeline: slots at T1/T26, shift at T40, no benign bump.
TL_SHIFT = sim.Timeline(t_shift=40, detected=True, bump=False, t_bump=None)
#: Same, with a benign bump at T20 (P1' published at T25).
TL_BUMP = sim.Timeline(t_shift=40, detected=True, bump=True, t_bump=20)
#: A silent shift: the version string never reaches "v2".
TL_SILENT = sim.Timeline(t_shift=40, detected=False, bump=False, t_bump=None)


# ---------------------------------------------------------------------------
# Helpers: scripted episode draws (§5.2)
# ---------------------------------------------------------------------------


class _ScriptedRng:
    """``random.Random`` stand-in that returns a scripted list of uniforms."""

    def __init__(self, values):
        self._values = list(values)

    def random(self) -> float:
        return self._values.pop(0)


class _ScriptedCase:
    """A case whose episode draws are a scripted list of ``(u_t, u_s, u_l)``."""

    base_p = 0.60

    def __init__(self, triples):
        self._flat = [u for triple in triples for u in triple]

    def draws(self, window: str, episode: int):
        return _ScriptedRng(self._flat)


TIMEOUT = (0.00, 0.00, 0.00)  # u_timeout < base_p -> timeout (u_stale low too)
SUCCESS = (0.99, 0.00, 0.00)  # u_timeout > p -> call succeeds, latency 200 ms


# ---------------------------------------------------------------------------
# §5.2 / §16.3: the v2 lease rule
# ---------------------------------------------------------------------------


def test_lease_refused_retry_costs_one_call_throttle_and_an_attempt():
    # P1 (``PEER_POLICY``): base 400 ms, 4 attempts -> every retry is refused.
    case = _ScriptedCase([TIMEOUT, SUCCESS, SUCCESS, SUCCESS, SUCCESS])
    ep = sim.run_episode_v2(PEER_POLICY, case, "immediate", 0)

    # One tool call per refused retry, each attempt consumed, all four spent.
    assert ep.tool_calls == PEER_POLICY.max_attempts == 4
    assert len(ep.steps) == 4
    assert ep.success is False
    assert ep.reason == "max_attempts"
    assert [s["event"] for s in ep.steps] == ["timeout"] + ["session_expired"] * 3
    # Wait + THROTTLE_MS per refused retry: 1500 + (400+100) + (800+100) + (1600+100).
    assert ep.elapsed_ms == TIMEOUT_MS + (400 + THROTTLE_MS) + (800 + THROTTLE_MS) + (1600 + THROTTLE_MS)

    # ``pending_stale`` is unchanged: the low ``u_stale`` of the refused attempts
    # would have produced a stale result in v1.
    assert "stale_discarded" not in [s["event"] for s in ep.steps]
    assert "stale_accepted" not in [s["event"] for s in ep.steps]


def test_lease_pending_stale_survives_a_refused_retry():
    # The same script in v1 consumes the stale arrival at attempt 2 and then
    # succeeds on the real call; in v2 the retry is refused and the episode fails.
    v1 = run_episode(PEER_POLICY, _ScriptedCase([TIMEOUT, SUCCESS, SUCCESS]), "immediate", 0)
    v2 = sim.run_episode_v2(
        PEER_POLICY, _ScriptedCase([TIMEOUT, SUCCESS, SUCCESS, SUCCESS, SUCCESS]), "immediate", 0
    )
    assert [s["event"] for s in v1.steps] == ["timeout", "stale_discarded", "ok"]
    assert v1.success is True and v2.success is False
    assert v2.tool_calls == 4 > v1.tool_calls == 2


def test_lease_does_not_apply_below_the_threshold():
    # P2 waits 150 and 300 ms (never > LEASE_MS) and the naive policy never backs
    # off, so v2 is simulated exactly as v1 on the same draws.
    for policy in (sim.P2_POLICY, naive_policy()):
        for index in (1, 2, 3):
            case = Case(11, index)
            for window in ("immediate", "delayed"):
                for episode in range(3):
                    e1 = run_episode(policy, case, window, episode)
                    e2 = sim.run_episode_v2(policy, case, window, episode)
                    assert (e1.success, e1.tool_calls, e1.elapsed_ms, e1.steps) == (
                        e2.success, e2.tool_calls, e2.elapsed_ms, e2.steps
                    )


def test_p1_fails_more_in_v2_than_in_v1():
    v1 = v2 = n = 0
    for index in range(1, 21):
        case = Case(7, index)
        for window in ("immediate", "delayed"):
            for episode in range(3):
                v1 += run_episode(PEER_POLICY, case, window, episode).success
                v2 += sim.run_episode_v2(PEER_POLICY, case, window, episode).success
                n += 1
    assert v1 / n > 0.85, v1 / n  # planned 0.947
    assert v2 / n < 0.50, v2 / n  # planned 0.250
    assert v1 - v2 > 0.4 * n


# ---------------------------------------------------------------------------
# §6.1 / §16.2: the ``expiry`` convention
# ---------------------------------------------------------------------------


def test_expiry_object_shape():
    obj = sim.expiry_object("v1")
    assert obj == {"convention": "tool-version/0", "tool": "web_search", "tool_version_neq": "v1"}
    assert sim.expiry_evaluable(obj) is True


def test_expiry_predicate_fires_only_on_a_different_version():
    pkt_expiry = sim.expiry_object("v1")
    assert sim.expiry_expired(pkt_expiry, "v1") is False
    assert sim.expiry_expired(pkt_expiry, "v1.1") is True
    assert sim.expiry_expired(pkt_expiry, "v2") is True
    # Symmetry: the packet is valid exactly when the versions are equal.
    for version in ("v1", "v1.1", "v2", "v3"):
        assert sim.expiry_expired(sim.expiry_object(version), version) is False


def test_expiry_predicate_is_not_evaluable_for_bad_objects():
    good = sim.expiry_object("v1")
    bad = [
        None,
        "v1",
        {},
        {k: v for k, v in good.items() if k != "convention"},  # convention missing
        dict(good, convention="tool-version/1"),  # different convention
        dict(good, tool="other_tool"),  # a tool the receiver does not use
        {k: v for k, v in good.items() if k != "tool_version_neq"},  # key missing
        dict(good, tool_version_neq=3),  # not a string
    ]
    for expiry in bad:
        assert sim.expiry_evaluable(expiry) is False, expiry
        # "never fires": None means not evaluable, not "valid".
        assert sim.expiry_expired(expiry, "v2") is None, expiry
        assert sim._valid_at({"expiry": expiry}, "v1") is False, expiry


def test_unevaluable_expiry_never_fires_and_is_logged(monkeypatch):
    # §6.1: the §5.4 feed never produces a non-evaluable object and a non-zero
    # count is a bug. Simulate such a producer: break every object in place from
    # T2 on, i.e. after D installed P1 at T1.
    original = sim.feed_at

    def patched(packets, tasks, t):
        feed = original(packets, tasks, t)
        if t >= 2:
            for packet in feed:
                packet["expiry"] = dict(sim.expiry_object("v1"), convention="tool-version/9")
        return feed

    monkeypatch.setattr(sim, "feed_at", patched)
    arm = sim.run_arm("D", 42, 1, TL_SHIFT, n_tasks=60)
    assert arm.policy_origin[0] == "packet"  # P1 installed at T1
    assert arm.expiry_events == []  # not evaluable -> never fires
    assert arm.reverts == []
    assert arm.expiry_unevaluable == 59  # logged at every task with a packet installed


# ---------------------------------------------------------------------------
# §5.3 / §16.5, §16.6: the version timeline
# ---------------------------------------------------------------------------


def _all_timelines():
    return [sim.draw_timeline(seed, run) for seed in sim.SEEDS for run in range(sim.RUNS)]


def test_version_timeline_ranges():
    timelines = _all_timelines()
    assert len(timelines) == 160  # §9: four seeds x 40 runs
    assert all(76 <= tl.t_shift <= 125 for tl in timelines)
    for tl in timelines:
        if tl.bump:
            assert tl.t_bump is not None and 31 <= tl.t_bump <= 70
        else:
            assert tl.t_bump is None


def test_version_timeline_silent_and_benign_rates():
    timelines = _all_timelines()
    silent = sum(1 for tl in timelines if not tl.detected) / len(timelines)
    benign = sum(1 for tl in timelines if tl.bump) / len(timelines)
    assert 0.15 <= silent <= 0.36, silent  # planned 0.25
    assert 0.40 <= benign <= 0.60, benign  # planned 0.50


def test_version_timeline_is_deterministic_per_seed_and_run():
    assert sim.draw_timeline(42, 3) == sim.draw_timeline(42, 3)
    assert sim.draw_timeline(42, 3) == sim.draw_timeline(42, 3)
    keys = {(tl.t_shift, tl.detected, tl.bump, tl.t_bump) for tl in _all_timelines()}
    assert len(keys) > 100  # draws really differ across (seed, run)


def test_version_and_regime_follow_the_shift():
    for tl in _all_timelines():
        assert tl.version_at(1) == sim.VERSION_V1
        assert tl.version_at(tl.t_shift - 1) != sim.VERSION_V2
        assert tl.regime_at(tl.t_shift - 1) == "v1"
        assert tl.regime_at(tl.t_shift) == "v2"
        if tl.detected:
            assert tl.version_at(tl.t_shift) == sim.VERSION_V2
        else:
            assert tl.version_at(tl.t_shift) != sim.VERSION_V2
        if tl.bump:
            assert tl.version_at(tl.t_bump) == sim.VERSION_V1_BUMP
            assert tl.version_at(tl.t_bump - 1) == sim.VERSION_V1


# ---------------------------------------------------------------------------
# §5.4: the peer feed
# ---------------------------------------------------------------------------


def test_feed_packets_ids_publication_tasks_and_p2_expiry():
    for tl in _all_timelines():
        run_seed = sim.run_seed_of(42, 1)
        packets = sim.feed_packets(run_seed, tl)
        pubs = sim.feed_publication_tasks(tl)
        assert len(packets) == len(pubs) == (3 if tl.bump else 2)
        assert pubs == sorted(pubs)
        assert pubs[0] == 1 and pubs[-1] == tl.t_shift + sim.PEER_LAG
        assert [p["id"] for p in packets] == [
            f"pkt-exp-{run_seed}-{suffix}" for suffix in (["p1", "p1b", "p2"] if tl.bump else ["p1", "p2"])
        ]
        # §5.4: P1 is P1' content; P2 is the post-shift policy; both carry the
        # §6.1 object naming the version the peer observed when it published.
        assert [p["content"] for p in packets[:-1]] == [packets[0]["content"]] * (len(packets) - 1)
        assert packets[0]["content"]["recovery_policy"]["base_backoff_ms"] == PEER_POLICY.base_backoff_ms
        assert packets[-1]["content"]["recovery_policy"]["base_backoff_ms"] == sim.P2_POLICY.base_backoff_ms
        for packet in packets:
            assert sim.expiry_evaluable(packet["expiry"])
            assert packet["provenance"]["source_ref"] == sim.PEER_SOURCE_REF
        assert packets[0]["expiry"]["tool_version_neq"] == sim.VERSION_V1
        if tl.bump:
            assert packets[1]["expiry"]["tool_version_neq"] == sim.VERSION_V1_BUMP
            assert pubs[1] == tl.t_bump + sim.PEER_LAG
        # P2 names the version observed at T_shift + 5, also after a silent shift.
        assert packets[-1]["expiry"]["tool_version_neq"] == tl.version_at(tl.t_shift + sim.PEER_LAG)
        assert (packets[-1]["expiry"]["tool_version_neq"] == sim.VERSION_V2) is tl.detected
        # Feed order is publication order, so ``feed_at`` is a prefix.
        for t in (1, tl.t_shift, tl.t_shift + sim.PEER_LAG, sim.TASKS):
            feed = sim.feed_at(packets, pubs, t)
            assert feed == packets[: len(feed)]
            assert all(pub <= t for pub in pubs[: len(feed)])


def test_feed_read_and_expiry_check_token_charges():
    tl = TL_SHIFT
    run_seed = sim.run_seed_of(42, 1)
    packets = sim.feed_packets(run_seed, tl)
    pubs = sim.feed_publication_tasks(tl)
    full_read = sim.feed_read_tokens(packets)
    assert full_read > 0
    # §7.2: the feed read excludes ``content``.
    assert full_read == sum(
        sim.approx_tokens({k: v for k, v in p.items() if k != "content"}) for p in packets
    )
    assert sim.feed_read_tokens(sim.feed_at(packets, pubs, 1)) < full_read
    check = sim.expiry_check_tokens(packets[0], "v2")
    assert check > 0 and check == sim.expiry_check_tokens(packets[0], "v2")
    assert sim.expiry_check_tokens(packets[0], "v2") != sim.expiry_check_tokens(packets[0], "v1.1")


# ---------------------------------------------------------------------------
# §7: arm steps
# ---------------------------------------------------------------------------


def _pkts_by_id(timeline, seed=42, run=1):
    packets = sim.feed_packets(sim.run_seed_of(seed, run), timeline)
    return {p["id"]: p for p in packets}


def test_c_never_reverts_and_keeps_the_stale_policy_across_the_shift():
    for timeline in (TL_SHIFT, TL_BUMP, TL_SILENT):
        arm = sim.run_arm("C", 42, 1, timeline, n_tasks=60)
        assert arm.reverts == []  # §7 step 3: C never reverts
        assert arm.expiry_events == []  # §7 step 2: C never fires
        assert arm.expiry_unevaluable == 0
        # §7.1: C keeps the stale P1 through the shift and only changes policy
        # at a slot (T51 for T_shift = 40).
        pre = [t for t in range(1, timeline.t_shift) if arm.policy_origin[t - 1] == "packet"]
        if pre:
            assert arm.policy_label[timeline.t_shift - 1] == P1_LABEL
            assert arm.policy_pub[timeline.t_shift - 1] < timeline.t_shift
        # §7.2: the feed read is charged at every slot and nowhere else for C.
        for t in range(1, 61):
            charged = arm.expiry_overhead[t - 1] > 0
            assert charged is (t in sim.SLOT_TASKS), t


def test_d_reverts_to_host_start_and_fetches_while_expired():
    timeline = TL_SHIFT
    arm = sim.run_arm("D", 42, 1, timeline, n_tasks=60)
    packets = _pkts_by_id(timeline)
    p1_id, p2_id = [p["id"] for p in sim.feed_packets(sim.run_seed_of(42, 1), timeline)]

    # The predicate fires exactly at the shift ("v1" != "v2").
    assert arm.expiry_events[0] == timeline.t_shift
    assert arm.expiry_events == [timeline.t_shift]
    assert arm.reverts == [{
        "task": timeline.t_shift, "packet_id": p1_id, "result": "expired", "policy_before": P1_LABEL,
    }]
    # Step 3: single-step revert to the host start policy, for tasks T_shift … T_shift+4.
    for t in range(timeline.t_shift, timeline.t_shift + sim.PEER_LAG):
        assert arm.policy_label[t - 1] == NAIVE_LABEL
        assert arm.policy_origin[t - 1] == "host_start"
        assert arm.policy_source[t - 1] is None
    # Step 4: the fetch happens at the first task where a valid packet exists
    # (T_shift + 5, the peer lag), not at the next scheduled slot.
    fetches = [g for g in arm.gate_attempts if g["source"] == "expired_fetch"]
    assert [g["task"] for g in fetches] == [timeline.t_shift + sim.PEER_LAG]
    assert fetches[0]["packet_id"] == p2_id
    if fetches[0]["status"] == "absorbed":
        assert arm.policy_label[timeline.t_shift + sim.PEER_LAG - 1] == P2_LABEL
        assert arm.policy_pub[timeline.t_shift + sim.PEER_LAG - 1] == timeline.t_shift + sim.PEER_LAG
    else:
        assert arm.policy_label[timeline.t_shift + sim.PEER_LAG - 1] == NAIVE_LABEL
    # §5.1 / §16.10: at most one gate attempt per arm per task.
    for t in range(1, 61):
        assert len(arm.gate_attempts_at(t)) <= 1, t
    # D never installs a packet that is not valid at the observed version.
    for t, pid in enumerate(arm.policy_source, start=1):
        if pid is not None:
            assert sim.expiry_expired(packets[pid]["expiry"], arm.observed_version[t - 1]) is False
    # §7.2: the per-task expiry check is charged exactly while a packet is installed.
    for t in range(1, 61):
        installed = arm.policy_origin[t - 1] == "packet"
        assert (arm.expiry_overhead[t - 1] > 0) is (installed or t in sim.SLOT_TASKS), t


def test_d_fetches_p1_prime_after_a_benign_bump_and_each_packet_once_per_state():
    timeline = TL_BUMP
    arm = sim.run_arm("D", 42, 1, timeline, n_tasks=60)
    packets = _pkts_by_id(timeline)
    p1b_id = [p["id"] for p in sim.feed_packets(sim.run_seed_of(42, 1), timeline)][1]

    # A benign bump is a false positive: an expiry event before T_shift.
    assert arm.expiry_events[0] == timeline.t_bump
    assert all(t <= timeline.t_bump for t in arm.expiry_events if t < timeline.t_shift)
    assert arm.expiry_events.count(timeline.t_bump) == 1
    fetch = [g for g in arm.gate_attempts if g["source"] == "expired_fetch"]
    assert fetch[0]["packet_id"] == p1b_id
    assert fetch[0]["task"] == timeline.t_bump + sim.PEER_LAG
    # §7.1: P1' has P1's content, so a slot after re-absorbing it is a no-op.
    if fetch[0]["status"] == "absorbed":
        assert arm.policy_label[timeline.t_bump + sim.PEER_LAG - 1] == P1_LABEL
        assert not [g for g in arm.gate_attempts if g["task"] == 26]
    # §16.8: each packet is tried once per expired state.
    seen = {}
    for gate in fetch:
        seen.setdefault(gate["packet_id"], []).append(gate["task"])
    for pid, tasks in seen.items():
        assert len(tasks) == len(set(tasks))
        for earlier, later in zip(tasks, tasks[1:]):
            assert any(earlier < event <= later for event in arm.expiry_events), (pid, earlier, later)
    # The revert is attributed to the packet id with the local result ``expired``.
    p1_id = [p["id"] for p in sim.feed_packets(sim.run_seed_of(42, 1), timeline)][0]
    assert {r["result"] for r in arm.reverts} == {"expired"}
    assert arm.reverts[0] == {
        "task": timeline.t_bump, "packet_id": p1_id, "result": "expired", "policy_before": P1_LABEL,
    }
    assert all(sim.expiry_evaluable(packets[r["packet_id"]]["expiry"]) for r in arm.reverts)


def test_v1_policy_stays_naive_but_packets_are_delivered():
    for timeline in (TL_SHIFT, TL_BUMP):
        arm = sim.run_arm("V1", 42, 1, timeline, n_tasks=60)
        # §11.1: derive() -> {} so nothing usable reaches the policy.
        assert set(arm.policy_label) == {NAIVE_LABEL}
        assert set(arm.policy_origin) == {"host_start"}
        assert arm.expiry_events == [] and arm.reverts == []
        # The packets are still delivered and gated.
        assert arm.gate_attempts, "V1 must still receive and gate the feed"
        assert all(g["status"] == "rejected" for g in arm.gate_attempts)
        # §7.2: V1 pays the feed read at slots only, like C.
        for t in range(1, 61):
            assert (arm.expiry_overhead[t - 1] > 0) is (t in sim.SLOT_TASKS), t


def test_v2_triggers_equal_ds_event_count_and_are_drawn_from_t2(full_run):
    seed, run, result = full_run
    d_arm = result["arms"]["D"]
    v2_arm = result["arms"]["V2"]
    triggers = result["v2_triggers"]
    assert result["detected"] and not result["bump"]  # D expires exactly once, at T_shift
    assert result["v2_k"] == len(d_arm.expiry_events) == len(v2_arm.expiry_events) >= 1
    assert triggers == sim.v2_trigger_tasks(seed, run, result["v2_k"])
    assert len(triggers) == len(set(triggers)) == result["v2_k"]
    assert triggers == sorted(triggers)
    assert all(2 <= t <= 200 for t in triggers)
    assert v2_arm.expiry_events == triggers
    assert sorted(triggers) == triggers
    # §7.3: only the trigger is removed; the validity filter of §7 steps 4-5 is
    # kept, so V2 only ever tries to absorb packets that are valid at the observed
    # version (an installed policy may still go stale between triggers: that is
    # exactly the ablation).
    packets = _pkts_by_id(result["timeline"], seed, run)
    assert v2_arm.gate_attempts
    for arm in (v2_arm, d_arm):
        for gate in arm.gate_attempts:
            version = arm.observed_version[gate["task"] - 1]
            assert sim.expiry_expired(packets[gate["packet_id"]]["expiry"], version) is False
    # §5.1: at most one gate attempt per arm per task, for every arm.
    for label, arm in result["arms"].items():
        for t in range(1, len(arm.successes) + 1):
            assert len(arm.gate_attempts_at(t)) <= 1, (label, t)


# ---------------------------------------------------------------------------
# §5 / §9: paired draws, determinism and the §8 record shape
# ---------------------------------------------------------------------------


def _first_detected_run_without_bump():
    for seed in sim.SEEDS:
        for run in range(sim.RUNS):
            timeline = sim.draw_timeline(seed, run)
            if timeline.detected and not timeline.bump:
                return seed, run
    raise AssertionError("no detected shift without a benign bump in the registered draws")


@pytest.fixture(scope="module")
def full_run():
    """One full 200-task run of all five arms (the expensive draw)."""
    seed, run = _first_detected_run_without_bump()
    return seed, run, sim.simulate_run(seed, run)


def test_arms_share_the_task_draws():
    timeline = TL_BUMP
    arms = {label: sim.run_arm(label, 42, 1, timeline, n_tasks=60) for label in ("A", "C", "D", "V1")}
    k = len(arms["D"].expiry_events)
    arms["V2"] = sim.run_arm(
        "V2", 42, 1, timeline, n_tasks=60, v2_triggers=sim.v2_trigger_tasks(42, 1, k, 60)
    )
    # V1 is the stub: nothing reaches the policy, so it runs exactly A's episodes.
    assert arms["A"].successes == arms["V1"].successes
    # Same policy in force at the same task -> the same six episode outcomes for
    # every arm: the draws are paired on ``Case(run_seed, t)``, not redrawn.
    for t in range(1, 61):
        by_policy = {}
        for label, arm in arms.items():
            by_policy.setdefault(arm.policy_label[t - 1], set()).add(arm.successes[t - 1])
        for policy_label, values in by_policy.items():
            assert len(values) == 1, (t, policy_label, values)
    # The arms do differ where their policies differ.
    assert any(len({arm.successes[t - 1] for arm in arms.values()}) > 1 for t in range(1, 61))


def test_determinism_same_seed_and_run_is_identical():
    a = sim.run_arm("D", 7, 3, TL_BUMP, n_tasks=60)
    b = sim.run_arm("D", 7, 3, TL_BUMP, n_tasks=60)
    assert a.successes == b.successes
    assert a.gate_attempts == b.gate_attempts
    assert a.expiry_events == b.expiry_events
    # The version timeline is a pure function of (seed, run).
    assert sim.draw_timeline(7, 3) == sim.draw_timeline(7, 3)
    assert sim.v2_trigger_tasks(7, 3, 4) == sim.v2_trigger_tasks(7, 3, 4)


def test_simulate_run_metadata_and_per_task_records(full_run):
    seed, run, result = full_run
    assert result["seed"] == seed and result["run"] == run
    assert result["run_seed"] == sim.run_seed_of(seed, run)
    assert result["n_tasks"] == sim.TASKS
    for key in ("t_shift", "detected", "silent", "bump", "t_bump", "t_p1b", "t_p2", "v2_k", "v2_triggers"):
        assert key in result, key
    assert result["t_shift"] == result["timeline"].t_shift
    assert result["silent"] is (not result["detected"])
    assert result["t_p2"] == result["t_shift"] + sim.PEER_LAG
    assert set(result["arms"]) == set(sim.ARM_ORDER)

    for label, arm in result["arms"].items():
        assert arm.arm == label and arm.seed == seed and arm.run == run
        n = len(arm.successes)
        assert n == sim.TASKS == len(arm.task_tokens) == len(arm.tool_calls) == len(arm.gate_tool_calls)
        assert n == len(arm.expiry_overhead) == len(arm.policy_source) == len(arm.policy_pub)
        assert n == len(arm.policy_label) == len(arm.policy_origin) == len(arm.observed_version)
        assert n == len(arm.regime)
        assert all(0 <= s <= sim.EPISODES_PER_TASK for s in arm.successes)
        assert all(t_call >= 0 for t_call in arm.tool_calls)
        assert all(r in ("v1", "v2") for r in arm.regime)
        records = arm.task_records()
        assert len(records) == n
        assert set(records[0]) == {
            "task", "successes", "sr", "policy_label", "policy_origin", "packet_id", "packet_pub",
            "tool_calls", "tokens", "expiry_overhead", "observed_version", "regime", "expiry_event",
            "gate_attempts",
        }
        assert [r["task"] for r in records] == list(range(1, n + 1))
        assert [r["expiry_event"] for r in records] == [t in set(arm.expiry_events) for t in range(1, n + 1)]


def test_endpoints_cover_every_arm(full_run):
    _seed, _run, result = full_run
    endpoints = sim.run_endpoints(result)
    assert set(endpoints["arms"]) == set(sim.ARM_ORDER)
    expected = {
        "arm", "recovery_window_sr", "recovery_window_tasks", "tasks_to_recover",
        "tasks_to_recover_censored", "stale_policy_uses_window", "stale_policy_uses_total",
        "task_tool_calls_window", "gate_tool_calls_window", "false_positive_expiries",
        "expiry_events", "expiry_unevaluable", "full_sequence_sr", "pre_shift_sr", "pre_ref_sr",
        "total_tokens", "eval_tokens", "gate_tokens", "expiry_overhead_tokens",
        "policy_changes", "attributed_changes",
        "expiry_reverts", "attributed_reverts", "content_policy_rejections", "final_policy",
        "extra_tool_calls",
    }
    for label, endpoint in endpoints["arms"].items():
        assert set(endpoint) == expected, label
        assert 0.0 <= endpoint["recovery_window_sr"] <= 1.0
        assert endpoint["recovery_window_tasks"] == sim.RECOVERY_WINDOW_K
        assert 0 <= endpoint["tasks_to_recover"] <= sim.TASKS_TO_RECOVER_CENSOR
        assert endpoint["content_policy_rejections"] == 0  # §5.4: a generator bug
        assert endpoint["expiry_unevaluable"] == 0  # §6.1: the feed never sends one
        # §8.2: attribution - every policy change and revert is traceable.
        assert endpoint["attributed_changes"] <= endpoint["policy_changes"]
        assert endpoint["attributed_reverts"] <= endpoint["expiry_reverts"]
    for label in ("A", "C", "V1"):
        assert endpoints["arms"][label]["expiry_events"] == 0
        assert endpoints["arms"][label]["expiry_reverts"] == 0
    assert endpoints["arms"]["A"]["expiry_overhead_tokens"] == 0
    assert endpoints["arms"]["C"]["expiry_overhead_tokens"] > 0
    # §8.2: total tokens = evaluation episodes + gate + §7.2 expiry charges.
    for label, endpoint in endpoints["arms"].items():
        arm = result["arms"][label]
        assert endpoint["eval_tokens"] == sum(arm.task_tokens)
        assert endpoint["expiry_overhead_tokens"] == sum(arm.expiry_overhead)
        assert endpoint["gate_tokens"] == sum(g["tokens"] for g in arm.gate_attempts)
        assert endpoint["total_tokens"] == (
            endpoint["eval_tokens"] + endpoint["gate_tokens"] + endpoint["expiry_overhead_tokens"]
        )
    assert endpoints["arms"]["A"]["gate_tokens"] == 0  # A receives no packets
    assert endpoints["arms"]["V2"]["false_positive_expiries"] == sum(
        1 for t in result["v2_triggers"] if t < result["t_shift"]
    )
