"""Selection experiment (docs/PREREG_SELECTION.md): pool, envelopes, selection
rules, arms, endpoints, decision rules, CLI writes and determinism."""

from __future__ import annotations

import json
import random
import subprocess
import sys
from collections import Counter
from fractions import Fraction
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ai_oriented_a2a import content_policy_rejection  # noqa: E402
from ai_oriented_a2a.recovery import parse_recovery_params  # noqa: E402

from experiments.selection import analysis, pool as poolmod, sim  # noqa: E402
from experiments.selection import run as runner  # noqa: E402
from experiments.selection.pool import (  # noqa: E402
    ENVELOPE_FIELDS,
    PoolPacket,
    build_pool,
    envelope_of,
    random_order,
    shuffle_envelopes,
    source_of,
)
from experiments.timeout_recovery import host as tr_host  # noqa: E402

SEL_DIR = REPO / "experiments" / "selection"


@pytest.fixture(scope="module")
def pool42():
    return build_pool(42, 0)


@pytest.fixture(scope="module")
def run42():
    return sim.simulate_run(42, 3, n_tasks=100)


# ---------------------------------------------------------------------------
# Registered constants
# ---------------------------------------------------------------------------


def test_registered_constants():
    assert sim.SEEDS == (42, 7, 2026, 31337) and sim.RUNS == 40 and sim.TASKS == 200
    assert sim.SLOT_TASKS == (1, 26, 51, 76)
    assert sim.plateau_window(200) == (151, 200)
    assert sim.DELTA == Fraction(1, 10) and sim.HISTORY_WEIGHT == 3
    assert poolmod.PRIOR_FLIP_P == 0.15 and poolmod.PRIOR_M_RANGE == (3, 12)
    assert analysis.HALF_WIDTH_FLAG == 0.065
    assert (sim.PILOT_SEED, sim.PILOT_RUNS, sim.PILOT_TASKS) == (42, (100, 101), 50)
    assert sim.PRIMARY_HOST == "naive_host" and sim.HOSTS == ("naive_host", "working_host")
    # The gate constants come from host.py unchanged (§5).
    assert tr_host.DSR_THRESHOLD == 0.05 and tr_host.ISOLATION_N == 3


def test_arm_mapping_table():
    names = {k: v[0] for k, v in sim.ARMS.items()}
    assert names == {
        "A": "no_sharing", "B": "local_file_gated", "C": "protocol_blind",
        "D1": "protocol_informed_history", "D2": "protocol_informed_claim",
        "V1": "protocol_blind_stub", "V2": "protocol_informed_history_shuffled",
    }
    sel = {k: v[2] for k, v in sim.ARMS.items()}
    assert sel == {"A": None, "B": "random", "C": "random", "D1": "history", "D2": "claim",
                   "V1": "random", "V2": "history"}
    assert sim.ARMS["V2"][3] == "shuffled" and sim.ARMS["B"][1] == "file" and sim.ARMS["V1"][1] == "stub"


# ---------------------------------------------------------------------------
# Pool (§6.1)
# ---------------------------------------------------------------------------


def test_pool_composition(pool42):
    assert len(pool42) == 50
    assert Counter(p.family for p in pool42) == {"effective": 3, "mediocre": 37, "harmful": 10}
    by_peer = Counter((p.peer, p.family) for p in pool42)
    assert by_peer[("peer-eff-1", "effective")] == 2 and by_peer[("peer-eff-1", "mediocre")] == 8
    assert by_peer[("peer-eff-2", "effective")] == 1 and by_peer[("peer-eff-2", "mediocre")] == 9
    assert by_peer[("peer-med-1", "mediocre")] == 10 and by_peer[("peer-med-2", "mediocre")] == 10
    assert by_peer[("peer-dis-1", "harmful")] == 10
    assert len({p.id for p in pool42}) == 50


def test_effective_positions_drawn_per_run():
    positions = {tuple(i for i, p in enumerate(build_pool(42, r)[:20]) if p.family == "effective") for r in range(6)}
    assert len(positions) > 1


def test_family_parameters(pool42):
    for p in pool42:
        params, _ = parse_recovery_params(p.packet["content"])
        if p.family == "effective":
            assert params["backoff"] is True and params["base_backoff_ms"] in (400, 800)
            assert params["max_attempts"] in (4, 5) and params["drop_stale_partials"] is True
        elif p.family == "mediocre":
            assert params["backoff"] is True and params["base_backoff_ms"] in (50, 100)
            assert params["max_attempts"] in (2, 3) and params["drop_stale_partials"] is False
        else:
            assert params["backoff"] is False and params["max_attempts"] in (6, None)
            assert params["drop_stale_partials"] is False


def test_pool_packets_pass_content_policy_and_schema(pool42):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((REPO / "schema" / "experience-packet.schema.json").read_text())
    for p in pool42:
        assert content_policy_rejection(p.packet) is None
        jsonschema.validate(p.packet, schema)


def test_ids_do_not_reveal_peer_or_family(pool42):
    for p in pool42:
        assert "peer" not in p.id and p.family not in p.id
        assert p.packet["type"] == "failure_mode"


def test_pool_is_deterministic():
    a = [p.packet for p in build_pool(7, 5)]
    b = [p.packet for p in build_pool(7, 5)]
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert json.dumps(a, sort_keys=True) != json.dumps([p.packet for p in build_pool(7, 6)], sort_keys=True)


# ---------------------------------------------------------------------------
# Envelopes (§6.2, §6.3)
# ---------------------------------------------------------------------------


def test_envelope_shapes(pool42):
    for p in pool42:
        prior = p.packet["prior_outcomes"]
        m = prior["absorbed_count"] + prior["rejected_count"]
        assert 3 <= m <= 12 and prior["last_status"] in ("absorbed", "rejected")
        assert p.packet["provenance"] == {"source_ref": f"peer:{p.peer}"}
        claim = p.packet["verification_claim"]
        assert claim["metric"] == "recovery_sr" and claim["repro"] == {
            "seed": 42, "n": 30, "model": "deterministic-rule-host-v1"}
        assert claim["expected_effect"] and 0.0 <= claim["value"] <= 1.0


def test_dishonest_peer_reports_good_outcomes(pool42):
    dis = [p for p in pool42 if p.peer == "peer-dis-1"]
    assert len(dis) == 10
    for p in dis:
        assert p.packet["prior_outcomes"]["last_status"] == "absorbed"
        assert 0.85 <= p.packet["verification_claim"]["value"] <= 0.95
        assert p.packet["provenance"]["source_ref"] == "peer:peer-dis-1"
    # Over many runs, Binomial(m, 0.9): about 90% absorbed.
    a = r = 0
    for run in range(10):
        for p in build_pool(42, run):
            if p.peer == "peer-dis-1":
                a += p.packet["prior_outcomes"]["absorbed_count"]
                r += p.packet["prior_outcomes"]["rejected_count"]
    assert 0.8 < a / (a + r) < 0.97


def test_harmful_packets_are_harmful_when_applied():
    case = tr_host.Case(123, 1)
    harmful = next(p for p in build_pool(42, 0) if p.family == "harmful")
    eff = next(p for p in build_pool(42, 0) if p.family == "effective")
    hs = [poolmod.packet_policy_sr(harmful.packet, tr_host.Case(9, i)) for i in range(30)]
    es = [poolmod.packet_policy_sr(eff.packet, tr_host.Case(9, i)) for i in range(30)]
    assert sum(es) / 30 > sum(hs) / 30 + 0.3
    assert case.case_id == "case-001"


def test_noisy_prior_outcomes_follow_gate_and_flip(monkeypatch):
    pkt = next(p for p in poolmod.base_packets(42, 0) if p.family == "effective").packet
    truth = None

    def run(phi):
        monkeypatch.setattr(poolmod, "PRIOR_FLIP_P", phi)
        return poolmod.honest_prior_outcomes(42, 0, pkt)

    clean = run(0.0)
    m = clean["absorbed_count"] + clean["rejected_count"]
    truth = [poolmod.prior_receiver_status(pkt, poolmod.prior_case(42, 0, pkt["id"], j)) == "absorbed"
             for j in range(m)]
    assert clean["absorbed_count"] == sum(truth)
    flipped = run(1.0)
    assert flipped["absorbed_count"] == m - sum(truth)
    assert flipped["last_status"] == ("rejected" if truth[-1] else "absorbed")


def test_prior_flips_happen_at_about_phi():
    flips = total = 0
    for run in range(4):
        for item in poolmod.base_packets(42, run):
            if item.peer == "peer-dis-1":
                continue
            rng = random.Random(tr_host.stable_int("prior-noise", 42, run, item.id))
            m = rng.randint(3, 12)
            for _ in range(m):
                flips += rng.random() < 0.15
                total += 1
    assert 0.09 < flips / total < 0.21


def test_prior_stream_is_independent_of_scored_tasks():
    run_seed = sim.run_seed_of(42, 0)
    pid = build_pool(42, 0)[0].id
    prior_seeds = {poolmod.prior_case(42, 0, pid, j).seed for j in range(12)}
    assert run_seed not in prior_seeds


def test_honest_claim_separates_families():
    effs, meds = [], []
    for run in range(3):
        for p in build_pool(42, run):
            if p.peer == "peer-dis-1":
                continue
            (effs if p.family == "effective" else meds).append(p.packet["verification_claim"]["value"])
    assert sum(effs) / len(effs) > sum(meds) / len(meds) + 0.3


# ---------------------------------------------------------------------------
# V2 envelope shuffle (§11.2)
# ---------------------------------------------------------------------------


def test_envelope_shuffle_keeps_content_and_permutes_envelopes(pool42):
    sh = shuffle_envelopes(pool42, 42, 0)
    assert len(sh) == 50
    for a, b in zip(pool42, sh):
        for key in ("id", "type", "absorption", "content", "version"):
            assert a.packet[key] == b.packet[key]
        assert (a.family, a.peer) == (b.family, b.peer)
    canon = lambda p: json.dumps({f: p[f] for f in ENVELOPE_FIELDS}, sort_keys=True)  # noqa: E731
    assert Counter(canon(p.packet) for p in pool42) == Counter(canon(p.packet) for p in sh)
    moved = sum(1 for a, b in zip(pool42, sh) if canon(a.packet) != canon(b.packet))
    assert moved > 25
    assert sh[0].packet is not pool42[0].packet
    assert json.dumps([p.packet for p in shuffle_envelopes(pool42, 42, 0)], sort_keys=True) == \
        json.dumps([p.packet for p in sh], sort_keys=True)


# ---------------------------------------------------------------------------
# Selection rules (§7)
# ---------------------------------------------------------------------------


def _pkt(a, r, src="peer:x", value=0.5, n=30, metric="recovery_sr"):
    return {"id": f"p{a}{r}{src}{value}", "prior_outcomes": {"absorbed_count": a, "rejected_count": r, "last_status": "absorbed"},
            "provenance": {"source_ref": src},
            "verification_claim": {"expected_effect": "x", "metric": metric, "value": value,
                                   "repro": {"seed": 1, "n": n, "model": "m"}}, "content": {}}


def test_history_score_formula():
    p = _pkt(4, 2, "peer:s")
    assert sim.history_score(p, {}) == Fraction(5, 8)
    assert sim.history_score(p, {"peer:s": [1, 0]}) == Fraction(4 + 3 + 1, 4 + 3 + 2 + 2)
    assert sim.history_score(p, {"peer:s": [0, 2]}) == Fraction(5, 4 + 2 + 6 + 2)
    assert sim.history_score(p, {"peer:other": [5, 0]}) == Fraction(5, 8)


def test_history_selection_learns_from_own_results():
    pkts = [_pkt(9, 1, "peer:liar"), _pkt(6, 2, "peer:honest")]
    rng = random.Random(0)
    assert sim.select_history([0, 1], pkts, {}, rng) == 0
    assert sim.select_history([0, 1], pkts, {"peer:liar": [0, 1]}, rng) == 1


def test_history_ties_use_seeded_rng():
    pkts = [_pkt(2, 2, f"peer:{i}") for i in range(6)]
    picks = {sim.select_history(list(range(6)), pkts, {}, random.Random(tr_host.stable_int("tie", 42, r, 0)))
             for r in range(20)}
    assert len(picks) > 1
    a = sim.select_history(list(range(6)), pkts, {}, random.Random(5))
    assert a == sim.select_history(list(range(6)), pkts, {}, random.Random(5))


def test_claim_ranking_eligible_first_by_value():
    pkts = [_pkt(0, 0, value=0.4), _pkt(0, 0, value=0.9), _pkt(0, 0, value=0.99, n=10),
            _pkt(0, 0, value=0.95, metric="other"), _pkt(0, 0, value=0.7)]
    order = sim.claim_ranking(list(range(5)), pkts, random.Random(1))
    assert order[:3] == [1, 4, 0]
    assert sorted(order[3:]) == [2, 3]


def test_random_order_shared_and_deterministic():
    o = random_order(42, 0)
    assert sorted(o) == list(range(50)) and o == random_order(42, 0) and o != random_order(42, 1)


def test_envelope_of_excludes_content(pool42):
    env = envelope_of(pool42[0].packet)
    assert "content" not in env and set(ENVELOPE_FIELDS) <= set(env)
    assert source_of(pool42[0].packet) == "peer:peer-eff-1"


# ---------------------------------------------------------------------------
# Arms (§4, §5)
# ---------------------------------------------------------------------------


def _arm(label, host="naive_host", seed=42, run=3, n_tasks=100):
    pool = build_pool(seed, run)
    pools = {"plain": pool, "shuffled": shuffle_envelopes(pool, seed, run)}
    cache = sim.EvalCache(sim.run_seed_of(seed, run))
    return sim.run_arm(label, host, seed, run, pools, random_order(seed, run), cache, n_tasks), pool


def test_slots_at_registered_tasks_and_never_twice():
    for label in ("B", "C", "D1", "D2", "V1", "V2"):
        r, _ = _arm(label)
        assert [s["task"] for s in r["slots"]] == [1, 26, 51, 76]
        assert len({s["packet_id"] for s in r["slots"]}) == 4
        assert all(t == 0 for i, t in enumerate(r["slot_tokens"]) if i + 1 not in (1, 26, 51, 76))


def test_random_arms_follow_the_shared_order():
    order = random_order(42, 3)
    for label in ("B", "C", "V1"):
        r, pool = _arm(label)
        assert [s["packet_id"] for s in r["slots"]] == [pool[i].id for i in order[:4]]


def test_b_and_c_have_identical_sr(run42):
    for host in sim.HOSTS:
        b, c = run42["hosts"][host]["B"], run42["hosts"][host]["C"]
        assert b["plateau_num"] == c["plateau_num"] and b["window_successes"] == c["window_successes"]


def test_no_sharing_never_changes_policy():
    r, _ = _arm("A")
    assert r["slots"] == [] and r["final_policy"] == tr_host.naive_policy().label()


def test_v1_stub_delivers_but_never_changes_policy():
    r, _ = _arm("V1")
    assert all(s["status"] == "rejected" and not s["policy_changed"] for s in r["slots"])
    assert r["final_policy"] == tr_host.naive_policy().label()
    m = sim.StubStrategyModel()
    assert m.derive("anything") == ({}, []) and m.calls == 1


def test_b_has_no_absorption_result_and_c_is_attributed():
    b, _ = _arm("B")
    c, _ = _arm("C")
    assert all(not s["attributed"] for s in b["slots"])
    assert all(s["attributed"] == (s["policy_changed"] and s["status"] == "absorbed") for s in c["slots"])


def test_selection_tokens_charged_only_to_envelope_readers():
    for label in ("B", "C", "V1"):
        assert sum(_arm(label)[0]["selection_tokens"]) == 0
    for label in ("D1", "D2", "V2"):
        r, pool = _arm(label)
        first = sum(tr_host.approx_tokens(envelope_of(p.packet)) for p in pool)
        assert r["selection_tokens"][0] == first or label == "V2"
        assert sum(r["selection_tokens"]) > 0


def test_working_host_starts_from_working_policy():
    r, _ = _arm("A", host="working_host")
    from experiments.timeout_recovery.packets import working_policy
    assert r["final_policy"] == working_policy().label()


def test_v2_uses_shuffled_sources_for_attribution():
    r, pool = _arm("V2")
    sh = {p.id: p for p in shuffle_envelopes(pool, 42, 3)}
    for s in r["slots"]:
        assert s["source_ref"] == source_of(sh[s["packet_id"]].packet)
        assert s["family"] == sh[s["packet_id"]].family


# ---------------------------------------------------------------------------
# Endpoints (§8)
# ---------------------------------------------------------------------------


def test_plateau_definition():
    # windows out of 150; plateau_SR = 0.9 -> threshold 0.8 = 120/150
    assert sim.plateau_reached([50, 130, 125, 140, 135, 135, 135, 135], 270, 300) == 2
    assert sim.plateau_reached([130] * 8, 270, 300) == 1
    # one dip inside a three-window run pushes the plateau later
    assert sim.plateau_reached([130, 130, 100, 130, 130, 130, 130, 130], 270, 300) == 4
    # W7 and W8 differ by more than 2 delta: not reached
    assert sim.plateau_reached([0, 0, 0, 0, 0, 0, 150, 110], 260, 300) is None
    # exact boundary counts as reached (>=)
    assert sim.plateau_reached([120] * 8, 270, 300) == 1


def test_endpoints_from_successes():
    succ = [0] * 50 + [6] * 150
    toks = list(range(200))
    e = sim.endpoints({"successes": succ, "tokens": toks, "slot_tokens": [0] * 200, "selection_tokens": [0] * 200,
                       "slots": [], "final_policy": "x"})
    assert e["plateau_num"] == 300 and e["plateau_den"] == 300 and e["plateau_sr"] == 1.0
    assert e["window_successes"] == [0, 0, 150, 150, 150, 150, 150, 150]
    assert e["plateau_window_reached"] == 3 and e["tasks_to_plateau"] == 75
    assert e["tokens_to_plateau"] == sum(range(75)) and e["total_tokens"] == sum(range(200))
    assert e["auc"] == pytest.approx(0.75) and not e["plateau_not_reached"]


def test_censored_plateau():
    succ = [0] * 150 + [6] * 25 + [2] * 25
    e = sim.endpoints({"successes": succ, "tokens": [1] * 200, "slot_tokens": [0] * 200,
                       "selection_tokens": [0] * 200, "slots": [], "final_policy": "x"})
    assert e["plateau_not_reached"] and e["tasks_to_plateau"] == 200 and e["tokens_to_plateau"] == 200


def test_harmful_absorption_counts(run42):
    for host in sim.HOSTS:
        for label, e in run42["hosts"][host].items():
            ab = e["absorption"]
            assert sum(ab[f]["selected"] for f in ab) == (0 if label == "A" else 4)
            for f in ab:
                assert ab[f]["absorbed"] + ab[f]["rolled_back"] <= ab[f]["selected"]
            assert e["content_policy_rejections"] == 0


# ---------------------------------------------------------------------------
# Analysis and decision rules (§11, §12)
# ---------------------------------------------------------------------------


def test_stratified_bootstrap_is_exact_for_constant_strata():
    est, lo, hi = analysis.stratified_bootstrap({1: [3] * 5, 2: [3] * 7}, 300, ("h", "c"))
    assert est == lo == hi == Fraction(3, 300)


def test_stratified_bootstrap_resamples_within_seed():
    # Stratum 1 is all 0, stratum 2 all 30: every resample keeps the 1:1 mix.
    est, lo, hi = analysis.stratified_bootstrap({1: [0] * 10, 2: [30] * 10}, 300, ("h", "c"))
    assert est == lo == hi == Fraction(15, 300)
    a = analysis.stratified_bootstrap({1: [0, 30, 60], 2: [3, 9]}, 300, ("h", "c"))
    assert a == analysis.stratified_bootstrap({1: [0, 30, 60], 2: [3, 9]}, 300, ("h", "c"))
    assert a != analysis.stratified_bootstrap({1: [0, 30, 60], 2: [3, 9]}, 300, ("h", "other"))


F = Fraction


@pytest.mark.parametrize("est,lo,hi,rule", [
    (F(15, 100), F(5, 100), F(25, 100), "R1a"),
    (F(10, 100), F(1, 100), F(19, 100), "R1a"),
    (F(9, 100), F(1, 100), F(17, 100), "R1b"),
    (F(-9, 100), F(-17, 100), F(-1, 100), "R3"),
    (F(0), F(-5, 100), F(5, 100), "R2"),
    (F(0), F(-10, 100), F(10, 100), "R2"),
    (F(2, 100), F(-5, 100), F(11, 100), "R4"),
    (F(0), F(0), F(0), "R2"),
])
def test_rule_table(est, lo, hi, rule):
    assert analysis.rule_of(est, lo, hi)[0] == rule


def test_rule_labels_verbatim():
    assert analysis.LABEL_R1A == "supported: envelope-informed selection improves plateau SR by at least the margin"
    assert analysis.LABEL_R1B == "positive but below the 0.10 margin; not practically significant"
    assert analysis.LABEL_R3 == "envelope-informed selection is worse"
    assert analysis.LABEL_R2 == "equivalent within ±0.10 ΔSR"
    assert analysis.LABEL_R4 == "undetermined"
    assert "support" not in analysis.LABEL_R1B


def _c(est, lo, hi):
    return {"estimate": F(est), "lo": F(lo), "hi": F(hi)}


def test_v1_and_v2_outcomes():
    assert analysis.v1_outcome(_c("0.1", "0.01", "0.2")) == "pass"
    assert analysis.v1_outcome(_c("0.1", "0", "0.2")) == "fail"
    assert analysis.v2_outcome(_c("0", "-0.05", "0.05")) == "pass"
    assert analysis.v2_outcome(_c("0.1", "0.01", "0.2")) == "VOID"
    assert analysis.v2_outcome(_c("0", "-0.2", "0.2")) == "inconclusive"
    assert analysis.v2_outcome(_c("-0.1", "-0.15", "-0.05")) == "inconclusive"
    assert analysis.v2_outcome(_c("0.05", "0", "0.1")) == "pass"


def test_verdict_rule_order():
    main = _c("0.2", "0.1", "0.3")
    assert analysis.verdict(main, "fail", "pass")["headline"] == "VOID"
    assert analysis.verdict(main, "pass", "VOID")["headline"] == "VOID"
    v = analysis.verdict(main, "pass", "inconclusive")
    assert v["headline"] == "undetermined" and v["information_rule"] == "R1a"
    assert analysis.verdict(main, "pass", "pass")["headline"] == analysis.LABEL_R1A
    r1b = analysis.verdict(_c("0.05", "0.01", "0.09"), "pass", "pass")
    assert r1b["rule"] == "R1b" and r1b["headline"] == analysis.LABEL_R1B


def test_analyse_on_small_sample(run42):
    other = sim.simulate_run(7, 3, n_tasks=100)
    res = analysis.analyse([run42, other])
    h = res["headline"]
    assert h["host"] == "naive_host" and h["contrast"] == "D1-C"
    assert set(res["hosts"]) == {"naive_host", "working_host"}
    assert set(res["hosts"]["naive_host"]["verdicts"]) == {"D1-C", "D2-C"}
    c = res["hosts"]["naive_host"]["contrasts"]["D1-C"]
    diffs = [r["hosts"]["naive_host"]["D1"]["plateau_sr"] - r["hosts"]["naive_host"]["C"]["plateau_sr"]
             for r in (run42, other)]
    assert float(c["estimate"]) == pytest.approx(sum(diffs) / 2)
    json.dumps(analysis.jsonable(res))


# ---------------------------------------------------------------------------
# Determinism and CLI (WP0: nothing written by default)
# ---------------------------------------------------------------------------


def test_simulate_run_is_deterministic(run42):
    again = sim.simulate_run(42, 3, n_tasks=100)
    assert json.dumps(again, sort_keys=True) == json.dumps(run42, sort_keys=True)


def test_run_all_does_not_depend_on_jobs():
    a = runner.run_all([42], [100, 101], 50, jobs=1)
    b = runner.run_all([42], [100, 101], 50, jobs=2)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _snapshot():
    files = sorted(p for p in SEL_DIR.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    return {str(p): p.read_bytes() for p in files}


def test_plain_cli_run_writes_nothing():
    before = _snapshot()
    proc = subprocess.run([sys.executable, "-m", "experiments.selection.run", "--pilot"],
                          cwd=REPO, capture_output=True, text=True, check=True)
    assert proc.stdout.startswith("strategy model: deterministic-rule-host-v1\n")
    assert "wrote " not in proc.stderr and "host naive_host" in proc.stdout
    assert _snapshot() == before


def test_cli_writes_only_where_asked(tmp_path):
    before = _snapshot()
    res = tmp_path / "R.md"
    summ = tmp_path / "s"
    rc = runner.main(["--seeds", "42", "--runs", "2", "--tasks", "50", "--results", str(res),
                      "--summary-dir", str(summ), "--commit", "test"])
    assert rc == 0 and res.exists() and (summ / "summary.json").exists() and (summ / "runs.jsonl").exists()
    rows = [json.loads(line) for line in (summ / "runs.jsonl").read_text().splitlines()]
    assert len(rows) == 2 * 2 and rows[0]["arms"] == list(sim.ARM_ORDER)
    assert _snapshot() == before
    text = res.read_text()
    assert "## Pilot" in text and "Amendments: none" in text


# ---------------------------------------------------------------------------
# Committed results (generated; consistent with each other and with §14)
# ---------------------------------------------------------------------------

FORBIDDEN = ("no measurable increment", "no effect", "protocol has no effect", "accountable")


def _committed():
    md = SEL_DIR / "SELECTION_RESULTS.md"
    summ = SEL_DIR / "summaries" / "summary.json"
    runs = SEL_DIR / "summaries" / "runs.jsonl"
    if not (md.exists() and summ.exists() and runs.exists()):
        pytest.skip("committed selection results not present")
    return md.read_text(encoding="utf-8"), json.loads(summ.read_text()), \
        [json.loads(x) for x in runs.read_text().splitlines()]


def test_committed_results_are_consistent():
    md, summ, rows = _committed()
    assert summ["seeds"] == [42, 7, 2026, 31337] and summ["runs"] == [0, 39] and summ["tasks"] == 200
    assert len(rows) == 160 * 2
    head = summ["result"]["headline"]
    assert f"**Verdict: `{head['verdict']['headline']}`**" in md
    assert f"V1 content-stub check (C − V1 lower bound > 0): **{head['v1']}**" in md
    assert f"**{head['v2']}**" in md
    # Recompute D1 - C on naive_host from runs.jsonl.
    d = [(r["plateau_successes"][3] - r["plateau_successes"][2]) / r["plateau_episodes"]
         for r in rows if r["host"] == "naive_host"]
    assert round(sum(d) / len(d), 4) == summ["result"]["hosts"]["naive_host"]["contrasts"]["D1-C"]["estimate"]
    for r in rows:
        assert r["slots"][0] == ""  # A has no slots
        assert all(len(s.split()) == 4 for s in r["slots"][1:])


def test_committed_results_follow_wording_rules():
    md, summ, _ = _committed()
    low = md.lower()
    for phrase in FORBIDDEN:
        assert phrase not in low
    assert "single-project format, not yet an interoperable protocol" in md
    assert "web_search" in md and "rule-parser receiver" in md
    if summ["result"]["headline"]["verdict"]["rule"] == "R1b":
        assert "Verdict: `supported" not in md


def test_committed_results_reproduce_for_one_seed_subset():
    """Rerun 2 of the 160 runs and compare their rows byte for byte."""
    _md, _summ, rows = _committed()
    fresh = runner.run_rows(runner.run_all([2026], [0, 39], 200))
    want = [r for r in rows if r["seed"] == 2026 and r["run"] in (0, 39)]
    key = lambda r: (r["run"], r["host"])  # noqa: E731
    assert sorted(fresh, key=key) == sorted(want, key=key)
