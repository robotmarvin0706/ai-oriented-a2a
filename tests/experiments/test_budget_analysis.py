"""Unit tests for ``experiments.budget.analysis`` (PREREG_BUDGET §8, §10-§12).

Fast: every run record here is synthetic endpoint data. No simulation, no stream
construction.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

import pytest

from experiments.budget.analysis import (
    C_HARM,
    LABEL_R1A,
    LABEL_R1B,
    LABEL_R2,
    LABEL_R3_PREFIX,
    LABEL_R4,
    LABEL_REPORTED,
    LABEL_VOID,
    RULE_NO_VERDICT,
    SR_HALF_WIDTH_PLAN,
    TOK_HALF_WIDTH_FLAG,
    analyse,
    decide,
    contrast,
    observed_rates,
    paired_bootstrap,
    rule_of,
    v1_check,
    v2_check,
)

FAMILIES = ("effective", "mediocre", "harmful")
ARMS = ("A", "C", "D1", "D2", "V1", "V2")


def endpoint(arm: str, seed: int = 42, run: int = 0, **kw: Any) -> Dict[str, Any]:
    """One arm's endpoint dict with the keys of ``sim.endpoints``."""
    plateau = kw.pop("plateau_SR", 0.60)
    total = kw.pop("total_tokens", 1000.0)
    gate = kw.pop("gate_tokens", 150.0)
    repro = kw.pop("repro_overhead_tokens", 50.0)
    task = kw.pop("task_tokens", total - gate - repro)
    harm = kw.pop("harmful_absorbed", False)
    ep: Dict[str, Any] = {
        "arm": arm,
        "name": arm,
        "seed": seed,
        "run": run,
        "k_e": kw.pop("k_e", 1),
        "k_s": kw.pop("k_s", 1),
        "plateau_SR": plateau,
        "full_sequence_SR": kw.pop("full_sequence_SR", plateau),
        "task_tokens": task,
        "gate_tokens": gate,
        "repro_overhead_tokens": repro,
        "verification_tokens": gate + repro,
        "total_tokens": task + gate + repro,
        "resident_context_tokens_T100": 1000,
        "harmful_absorbed": bool(harm),
        "harmful_installations": kw.pop("harmful_installations", 0),
        "persistent_harm": kw.pop("persistent_harm", False),
        "tasks_on_harmful_policy": kw.pop("tasks_on_harmful_policy", 0),
        "harmful_fast_accepted": 0,
        "harmful_spot_check_passed": 0,
        "harmful_gate_absorbed": 0,
        "harmful_rolled_back": 0,
        "n_fast_reject": kw.pop("n_fast_reject", 0),
        "n_fast_accept": kw.pop("n_fast_accept", 0),
        "fast_path_rate": kw.pop("fast_path_rate", 0.0),
        "false_fast_path": kw.pop("false_fast_path", 0),
        "spot_check_pass": {f: 0 for f in FAMILIES},
        "spot_check_fail": {f: 0 for f in FAMILIES},
        "escalations": 0,
        "probation_rollbacks_true": 0,
        "probation_rollbacks_false": 0,
        "probation_passed": 0,
        "probation_superseded": 0,
        "budget_rejections": 0,
        "rollbacks": {"gate": 0, "probation": 0, "total": 0},
        "policy_changes": 0,
        "gate_attempts": 0,
        "attributed": {"policy_changes": 0, "fast_rejects": 0,
                       "probation_rollbacks": 0, "events": 0, "rate": 1.0},
        "flagged_sources": [],
        "final_policy": "naive",
    }
    ep.update(kw)
    return ep


Override = Union[Dict[str, Any], Callable[[int, int], Dict[str, Any]]]


def synthetic_runs(overrides: Dict[str, Override], seeds: Sequence[int] = (42, 7),
                   per_seed: int = 1, base: Optional[Dict[str, Any]] = None
                   ) -> List[Dict[str, Any]]:
    """``run_endpoints``-shaped records with per-arm overrides (dict or callable)."""
    runs: List[Dict[str, Any]] = []
    for si, seed in enumerate(seeds):
        for ri in range(per_seed):
            arms: Dict[str, Any] = {}
            for arm in ARMS:
                shared = {} if base is None else dict(base)
                spec = overrides.get(arm)
                if callable(spec):
                    arms[arm] = endpoint(arm, seed, ri, **{**shared, **spec(si, ri)})
                else:
                    arms[arm] = endpoint(arm, seed, ri, **{**shared, **(spec or {})})
            runs.append({"seed": seed, "run": ri, "arms": arms})
    return runs


def flat_sync(**per_arm: Dict[str, Any]) -> Dict[str, Override]:
    """Every arm gets the same override unless its own is given."""
    return dict(per_arm)


#: A D2 result that meets R1a: half the tokens, same plateau SR, no harm, V1 passes,
#: V2 inconclusive-free (V2 fails on plateau SR decisively).
R1A_ARMS: Dict[str, Override] = {
    "C": {"total_tokens": 1000.0, "plateau_SR": 0.60},
    "D2": {"total_tokens": 500.0, "plateau_SR": 0.60},
    "D1": {"total_tokens": 500.0, "plateau_SR": 0.60},
    "V1": {"total_tokens": 1000.0, "plateau_SR": 0.30},
    "V2": {"total_tokens": 1000.0, "plateau_SR": 0.40},
    "A": {"total_tokens": 1100.0, "plateau_SR": 0.55},
}
# ---------------------------------------------------------------------------
# §8.1 statistics
# ---------------------------------------------------------------------------


def test_rel_token_reduction_is_ratio_of_sums_not_mean_of_ratios():
    totals_c = [1000.0, 1000.0, 1000.0, 100.0]
    totals_d = [500.0, 1000.0, 1000.0, 50.0]
    arms: Dict[str, Override] = {
        "C": lambda si, ri: {"total_tokens": totals_c[si * 2 + ri]},
        "D2": lambda si, ri: {"total_tokens": totals_d[si * 2 + ri]},
        "V2": {"total_tokens": 1000.0, "plateau_SR": 0.60},
        "V1": {"plateau_SR": 0.30},
    }
    out = analyse(synthetic_runs(arms, per_seed=2), b=200)
    est = out["primary"]["tokens"]["rel_token_reduction"]["estimate"]
    assert est == pytest.approx(1.0 - sum(totals_d) / sum(totals_c), abs=1e-6)
    mean_of_ratios = sum(1 - d / c for d, c in zip(totals_d, totals_c)) / 4
    assert est != pytest.approx(mean_of_ratios, abs=1e-6)


def test_per_seed_and_formula_of_token_parts():
    out = analyse(synthetic_runs(R1A_ARMS), b=100)
    parts = out["report_only"]["token_parts_relative_reduction"]["D2-C"]
    assert set(parts) == {"gate_tokens", "repro_overhead_tokens",
                          "verification_tokens", "task_tokens"}
    # D2 total 500 = task 300 + gate 150 + repro 50; C total 1000 -> 1 - 500/1000.
    assert parts["gate_tokens"]["estimate"] == pytest.approx(0.0)
    assert out["primary"]["tokens"]["rel_token_reduction"]["estimate"] == pytest.approx(0.5)
    assert out["per_seed"]["42"]["rel_token_reduction"] == pytest.approx(0.5)
    assert out["per_seed"]["42"]["delta_SR"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Bootstrap: determinism and stratification
# ---------------------------------------------------------------------------


def test_bootstrap_is_deterministic():
    runs = synthetic_runs(R1A_ARMS)
    a = json.dumps(analyse(runs, b=150), sort_keys=True)
    b = json.dumps(analyse(runs, b=150), sort_keys=True)
    assert a == b


def test_bootstrap_resamples_stay_within_seeds():
    # Every run of seed 42 has a +0.20 SR difference, every run of seed 7 has 0.0.
    arms: Dict[str, Override] = {
        "C": {"plateau_SR": 0.40},
        "D2": lambda si, ri: {"plateau_SR": 0.60 if si == 0 else 0.40},
        "V1": {"plateau_SR": 0.20},
        "V2": {"plateau_SR": 0.20},
    }
    runs = synthetic_runs(arms, seeds=(42, 7), per_seed=2)
    stats, meta = paired_bootstrap(runs, "D2", "C", "D2-C")
    assert meta["stratified"] is True
    assert meta["per_seed"] == {"42": 2, "7": 2}
    assert stats["plateau_SR"]["estimate"] == pytest.approx(0.10)
    # Stratified resampling keeps 2 runs of each seed: the mean cannot move.
    assert stats["plateau_SR"]["lo"] == pytest.approx(0.10)
    assert stats["plateau_SR"]["hi"] == pytest.approx(0.10)


def test_small_strata_fall_back_and_do_not_crash():
    arms: Dict[str, Override] = {
        "C": lambda si, ri: {"k_e": 0 if si == 3 else 1, "k_s": 1},
        "D2": {"total_tokens": 800.0},
        "V1": {"plateau_SR": 0.30},
        "V2": {"total_tokens": 1000.0, "plateau_SR": 0.30},
    }
    runs = synthetic_runs(arms, seeds=(42, 7, 2026, 31337), per_seed=1)
    out = analyse(runs, b=100)
    assert out["n_runs"] == 4
    assert out["primary"]["stratified"] is False
    assert "fallback" in out["primary"]["bootstrap_note"]
    occupied = [s for s in out["strata"] if s["runs"]]
    empty = [s for s in out["strata"] if not s["runs"]]
    assert occupied and empty
    for s in occupied:
        assert s["stratified"] is False and s["bootstrap_note"]
    for s in empty:
        assert s["rule"] == RULE_NO_VERDICT and s["label"] == LABEL_REPORTED


# ---------------------------------------------------------------------------
# Rule table (§12)
# ---------------------------------------------------------------------------


def tok(est: float, lo: float, hi: float) -> Dict[str, float]:
    return {"estimate": est, "lo": lo, "hi": hi, "half_width": (hi - lo) / 2}


@pytest.mark.parametrize("est,lo,hi,ni,safe,rule,label", [
    (0.20, 0.10, 0.30, True, True, "R1a", LABEL_R1A),
    (0.05, 0.01, 0.09, True, True, "R1b", LABEL_R1B),
    (-0.20, -0.30, -0.05, True, True, "R3", f"{LABEL_R3_PREFIX} (more_tokens)"),
    (0.20, 0.10, 0.30, False, True, "R3",
     f"{LABEL_R3_PREFIX} (plateau_SR_not_non_inferior)"),
    (0.20, 0.10, 0.30, True, False, "R3", f"{LABEL_R3_PREFIX} (harm_cap_breached)"),
    (-0.20, -0.30, -0.05, False, False, "R3",
     f"{LABEL_R3_PREFIX} (more_tokens, plateau_SR_not_non_inferior, harm_cap_breached)"),
    (0.0, -0.05, 0.05, True, True, "R2", LABEL_R2),
    (0.05, -0.20, 0.30, True, True, "R4", LABEL_R4),
])
def test_every_rule_branch(est, lo, hi, ni, safe, rule, label):
    got_rule, got_label, reasons = rule_of(tok(est, lo, hi), ni, safe)
    assert (got_rule, got_label) == (rule, label)
    if rule == "R3":
        assert got_label.endswith("(" + ", ".join(reasons) + ")")


def test_ni_and_safe_boundaries(monkeypatch):
    import experiments.budget.analysis as an

    def fake(ni_lo: float, harm_hi: float):
        def _fake(runs, left, right, name, b=an.BOOTSTRAP_B):
            stats = {m: {"estimate": 0.0, "lo": 0.0, "hi": 0.0, "half_width": 0.0}
                     for m in an.RATIO_METRICS + an.DIFF_METRICS}
            stats["total_tokens"] = {"estimate": 0.5, "lo": 0.4, "hi": 0.6,
                                     "half_width": 0.1}
            stats["plateau_SR"] = {"estimate": ni_lo, "lo": ni_lo, "hi": ni_lo,
                                   "half_width": 0.0}
            stats["harmful_absorbed"] = {"estimate": 0.05, "lo": 0.0, "hi": harm_hi,
                                         "half_width": harm_hi / 2}
            meta = {"contrast": name, "left": left, "right": right, "runs": 1, "b": b,
                    "seed": "fake", "stratified": True, "note": "", "per_seed": {"42": 1}}
            return stats, meta
        return _fake

    runs = synthetic_runs(R1A_ARMS, seeds=(42,), per_seed=1)
    monkeypatch.setattr(an, "paired_bootstrap", fake(-0.05, C_HARM))
    c = an.contrast(runs, "D2", "C", "D2-C", role="primary")
    assert c["delta_SR"]["non_inferior"] is False       # lo == -delta_SR is not NI
    assert c["harm"]["safe"] is True                    # hi == c_harm is within the cap
    assert c["rule"] == "R3" and c["reasons"] == ["plateau_SR_not_non_inferior"]

    monkeypatch.setattr(an, "paired_bootstrap", fake(-0.0499, C_HARM + 0.0001))
    c = an.contrast(runs, "D2", "C", "D2-C", role="primary")
    assert c["delta_SR"]["non_inferior"] is True
    assert c["harm"]["safe"] is False
    assert c["rule"] == "R3" and c["reasons"] == ["harm_cap_breached"]

    monkeypatch.setattr(an, "paired_bootstrap", fake(-0.0499, C_HARM))
    c = an.contrast(runs, "D2", "C", "D2-C", role="primary")
    assert (c["rule"], c["label"]) == ("R1a", LABEL_R1A)
# ---------------------------------------------------------------------------
# Validity checks and their effect on the headline (§11, §12)
# ---------------------------------------------------------------------------


def test_r1a_end_to_end():
    out = analyse(synthetic_runs(R1A_ARMS), b=200)
    assert out["primary"]["rule"] == "R1a"
    assert out["primary"]["headline"] == LABEL_R1A
    assert out["decision"]["headline"] == LABEL_R1A
    assert out["v1"]["outcome"] == "pass"
    assert out["v2"]["outcome"] == "pass"
    assert out["noisier_than_planned"] is False
    assert out["saturation"]["saturated"] is False


def test_v1_failure_makes_the_result_void():
    arms = dict(R1A_ARMS)
    arms["V1"] = {"total_tokens": 1000.0, "plateau_SR": 0.70}
    out = analyse(synthetic_runs(arms), b=100)
    assert out["v1"]["outcome"] == "fail"
    assert out["primary"]["rule"] == "V"
    assert out["primary"]["headline"] == LABEL_VOID
    assert "V1" in out["decision"]["reason"]
    assert out["decision"]["rule_as_information"] == "R1a"


def test_v2_void_makes_the_result_void():
    arms = dict(R1A_ARMS)
    arms["V2"] = {"total_tokens": 500.0, "plateau_SR": 0.60}
    out = analyse(synthetic_runs(arms), b=100)
    assert out["v2"]["outcome"] == "VOID"
    assert out["primary"]["headline"] == LABEL_VOID
    assert "V2" in out["decision"]["reason"]


def test_v2_inconclusive_caps_the_headline_at_undetermined():
    arms: Dict[str, Override] = {
        "C": {"total_tokens": 1000.0, "plateau_SR": 0.60},
        "D2": {"total_tokens": 500.0, "plateau_SR": 0.60},
        "V1": {"total_tokens": 1000.0, "plateau_SR": 0.30},
        "V2": lambda si, ri: {"total_tokens": (700.0, 1300.0)[ri],
                              "plateau_SR": 0.60},
    }
    out = analyse(synthetic_runs(arms, per_seed=2), b=200)
    assert out["v2"]["outcome"] == "inconclusive"
    assert out["v2"]["components_hold"] == []
    assert out["decision"]["rule_as_information"] == "R1a"
    assert out["decision"]["label_as_information"] == LABEL_R1A
    assert out["decision"]["headline"] == LABEL_R4
    assert out["primary"]["label"] == LABEL_R1A


def test_v2_components_are_recorded():
    arms: Dict[str, Override] = {
        "C": {"total_tokens": 1000.0, "plateau_SR": 0.60},
        "D2": {"total_tokens": 500.0, "plateau_SR": 0.60},
        "V1": {"plateau_SR": 0.30},
        "V2": lambda si, ri: {"total_tokens": (700.0, 1300.0)[ri],
                              "plateau_SR": 0.40},
    }
    out = analyse(synthetic_runs(arms, per_seed=2), b=200)
    assert out["v2"]["components"]["plateau_sr_ci_upper_below_zero"] is True
    assert out["v2"]["components"]["token_ci_upper_below_delta_tok"] is False
    assert out["v2"]["components"]["harm_ci_lower_above_c_harm"] is False
    assert out["v2"]["components_hold"] == ["plateau_sr_ci_upper_below_zero"]
    assert out["v2"]["outcome"] == "pass"
    gap = v1_check(synthetic_runs(R1A_ARMS), b=100)
    assert gap["outcome"] == "pass"
    assert v2_check(synthetic_runs(arms, per_seed=2), b=200)["outcome"] == "pass"


# ---------------------------------------------------------------------------
# Labeling discipline (§12, §14)
# ---------------------------------------------------------------------------


def test_secondary_contrasts_never_carry_a_headline():
    out = analyse(synthetic_runs(R1A_ARMS), b=100)
    names = [c["contrast"] for c in out["secondary"]]
    assert names == ["D1-C", "D2-V2"]
    for c in out["secondary"] + out["strata"]:
        assert c["role"] == "secondary"
        assert c["headline"] is None
    for c in out["strata"]:
        assert c["stratum"] in ({"k_s": 1}, {"k_s": 0}, {"k_e": 1}, {"k_e": 0})
    assert out["primary"]["role"] == "primary"


def _strings(obj: Any):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for key, value in obj.items():
            yield str(key)
            yield from _strings(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            yield from _strings(value)


def test_report_only_metrics_have_no_verdict_wording():
    out = analyse(synthetic_runs(R1A_ARMS), b=100)
    rep = out["report_only"]
    assert rep["rule"] == RULE_NO_VERDICT
    assert rep["label"] == LABEL_REPORTED
    text = " ".join(_strings(rep)).lower()
    assert "supported" not in text
    assert "worse" not in text
    for name, parts in rep["token_parts_relative_reduction"].items():
        assert set(parts) == {"gate_tokens", "repro_overhead_tokens",
                              "verification_tokens", "task_tokens"}
        for stat in parts.values():
            assert set(stat) == {"estimate", "lo", "hi", "half_width"}
    assert out["c_vs_a"]["rule"] == RULE_NO_VERDICT
    assert "supported" not in json.dumps(out["c_vs_a"]).lower()


def test_report_only_per_arm_figures_and_arm_table():
    runs = synthetic_runs(R1A_ARMS)
    runs[0]["arms"]["D2"].update({"n_fast_reject": 6, "n_fast_accept": 2,
                                  "fast_path_rate": 8 / 40, "false_fast_path": 1,
                                  "persistent_harm": True,
                                  "tasks_on_harmful_policy": 3,
                                  "harmful_absorbed": True})
    out = analyse(runs, b=100)
    d2 = out["report_only"]["per_arm"]["D2"]
    assert d2["fast_path_events_total"] == 8
    assert d2["false_fast_path_rate"] == pytest.approx(0.125)
    assert d2["persistent_harm_runs"] == 1
    table = out["arms"]["D2"]
    assert table["harmful_absorption_rate"] == pytest.approx(0.5)
    assert table["persistent_harm_count"] == 1
    assert table["tasks_on_harmful_policy_total"] == 3
    assert table["fast_path_rate_mean"] == pytest.approx(8 / 40 / 2)
    assert out["observed"]["mismatched_share"] == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# Noise-floor and saturation flags (§10, §9.5)
# ---------------------------------------------------------------------------


def test_noisier_than_planned_flag():
    tight = analyse(synthetic_runs(R1A_ARMS), b=200)
    assert tight["noisier_than_planned"] is False
    assert tight["half_width"] < TOK_HALF_WIDTH_FLAG
    assert tight["delta_SR_half_width"] <= SR_HALF_WIDTH_PLAN
    arms: Dict[str, Override] = {
        "C": {"total_tokens": 1000.0, "plateau_SR": 0.60},
        "D2": lambda si, ri: {"total_tokens": (700.0, 1300.0)[ri], "plateau_SR": 0.60},
        "V1": {"plateau_SR": 0.30},
        "V2": {"plateau_SR": 0.30},
    }
    wide = analyse(synthetic_runs(arms, per_seed=2), b=200)
    assert wide["half_width"] > TOK_HALF_WIDTH_FLAG
    assert wide["noisier_than_planned"] is True
    assert wide["primary"]["tokens"]["noisier_than_planned"] is True


def test_saturation_flag_does_not_change_the_rule():
    arms: Dict[str, Override] = {
        "C": {"total_tokens": 1000.0, "plateau_SR": 0.95},
        "D2": {"total_tokens": 500.0, "plateau_SR": 0.95},
        "V1": {"total_tokens": 1000.0, "plateau_SR": 0.50},
        "V2": {"total_tokens": 1000.0, "plateau_SR": 0.95},
    }
    out = analyse(synthetic_runs(arms), b=100)
    assert out["saturation"]["saturated"] is True
    assert out["saturation"]["arms"]["C"]["outside_bounds"] is True
    assert out["primary"]["rule"] == "R1a"
    assert out["decision"]["headline"] == LABEL_R1A


def test_analyse_needs_at_least_one_run():
    with pytest.raises(ValueError):
        analyse([])


def test_decide_is_pure_and_keeps_both_states():
    out = analyse(synthetic_runs(R1A_ARMS), b=100)
    dec = decide(out["primary"], {"outcome": "fail"}, {"outcome": "pass"}, False)
    assert dec["label"] == LABEL_VOID and dec["headline"] == LABEL_VOID
    assert dec["rule_as_information"] == "R1a"
    dec = decide(out["primary"], {"outcome": "pass"}, {"outcome": "inconclusive"}, False)
    assert dec["headline"] == LABEL_R4 and dec["rule"] == "R1a"


# ---------------------------------------------------------------------------
# Stream-rate mapping and undefined relative reductions (labeling fixes)
# ---------------------------------------------------------------------------


def test_observed_rates_map_k_e_to_effective_and_k_s_to_dishonest():
    """Prereg §5.2: ``k_e`` = effective packet present, ``k_s`` = dishonest peer present."""
    runs = []
    for k_e, k_s in ((1, 1), (1, 0), (0, 1), (0, 1)):
        runs.append({"seed": 42, "run": len(runs), "k_e": k_e, "k_s": k_s,
                     "arms": {"C": endpoint("C"), "D2": endpoint("D2")}})
    obs = observed_rates(runs)
    # k_e = 1 in 2 of 4 runs, k_s = 1 in 3 of 4 runs.
    assert obs["k_e_share"] == pytest.approx(0.5)
    assert obs["k_s_share"] == pytest.approx(0.75)
    assert obs["effective_packet_share"] == obs["k_e_share"]
    assert obs["dishonest_peer_share"] == obs["k_s_share"]
    assert obs["effective_packet_share"] != obs["dishonest_peer_share"]
    assert obs["planned"] == {"k_e_share": 0.5, "k_s_share": 0.5, "mismatched_share": 0.25}


def test_repro_overhead_relative_reduction_is_none_when_c_pays_none():
    """C is never charged repro overhead: the ratio of paired sums is undefined, not 0."""
    arms: Dict[str, Override] = {
        "C": {"total_tokens": 1200.0, "gate_tokens": 1000.0, "repro_overhead_tokens": 0.0,
              "plateau_SR": 0.60},
        "D2": {"total_tokens": 700.0, "gate_tokens": 450.0, "repro_overhead_tokens": 50.0,
               "plateau_SR": 0.60},
        "V1": {"total_tokens": 1000.0, "gate_tokens": 1000.0, "repro_overhead_tokens": 0.0,
               "plateau_SR": 0.30},
        "V2": {"total_tokens": 1000.0, "gate_tokens": 1000.0, "repro_overhead_tokens": 0.0,
               "plateau_SR": 0.40},
        "D1": {"total_tokens": 500.0, "gate_tokens": 500.0, "repro_overhead_tokens": 0.0,
               "plateau_SR": 0.60},
        "A": {"total_tokens": 1100.0, "gate_tokens": 1100.0, "repro_overhead_tokens": 0.0,
              "plateau_SR": 0.55},
    }
    c = contrast(synthetic_runs(arms), "D2", "C", "D2-C", role="primary", b=100)
    assert c["parts"]["repro_overhead_tokens"] is None
    assert c["parts_undefined"] == ["repro_overhead_tokens"]
    assert c["parts_left_means"]["repro_overhead_tokens"] == pytest.approx(50.0)
    # Every part whose C-side sum is non-zero stays a regular estimate.
    for part in ("verification_tokens", "task_tokens", "gate_tokens"):
        assert set(c["parts"][part]) == {"estimate", "lo", "hi", "half_width"}
